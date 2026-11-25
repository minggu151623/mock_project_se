"""Kiểm thử PUB-01→05: Website công khai, gửi yêu cầu dịch vụ, token >= 128 bit,
tra cứu tiến độ theo BR-18, giới hạn tần suất (rate limit) và quản trị CMS (BE3).
"""

import hashlib
import sqlite3

import pytest

from backend import assignments, sales, trip_ops
from backend.db import write_tx
from tests import factories as f
from tests.factories import ADM, DSP, SAL


def _transport_payload():
    return {
        "service_type": "TRANSPORT",
        "contact": {
            "name": "Công ty TNHH Sao Mai",
            "contact_name": "Lê Hoàng Nam",
            "phone": "0903456789",
            "email": "nam@saomai.test",
        },
        "service_data": {
            "pickup_address": "10 Nguyễn Văn Linh, Quận 7",
            "delivery_address": "KCN VSIP 1, Bình Dương",
            "cargo_description": "Hàng điện gia dụng đóng thùng",
            "cargo_weight_kg": 1200,
            "planned_pickup_at": f.iso(8),
            "committed_end": f.iso(12),
        },
        "data_use_acknowledged": True,
    }


def test_cms_lifecycle_html_sanitization_and_protect_all_three_services(client, conn):
    # Tạo 3 trang dịch vụ PUBLISHED + 1 bài nháp DRAFT + 1 bài ẩn HIDDEN.
    services = []
    for idx, stype in enumerate(("TRANSPORT", "RENTAL", "WAREHOUSE"), start=1):
        res = client.post(
            "/api/admin/cms/contents",
            headers=ADM,
            json={
                "slug": f"dich-vu-{stype.lower()}",
                "content_type": "SERVICE",
                "service_type": stype,
                "title": f"Dịch vụ {stype}",
                "summary": f"Tóm tắt {stype}",
                "body_html": (
                    f"<h2>{stype}</h2><p>An toàn</p>"
                    '<script>alert("xss")</script>'
                    '<a href="javascript:evil()" onclick="steal()">Link xấu</a>'
                    '<a href="https://example.com" title="Tốt">Link tốt</a>'
                ),
                "sort_order": idx,
                "status": "PUBLISHED",
            },
        )
        assert res.status_code == 201, res.text
        services.append(res.json())

    draft = client.post(
        "/api/admin/cms/contents",
        headers=ADM,
        json={
            "slug": "bai-nhap-noi-bo",
            "content_type": "COMPANY",
            "title": "Bài nháp nội bộ",
            "body_html": "<p>Chưa công bố</p>",
            "status": "DRAFT",
        },
    ).json()

    hidden = client.post(
        "/api/admin/cms/contents",
        headers=ADM,
        json={
            "slug": "bai-da-an",
            "content_type": "FAQ",
            "title": "FAQ đã ẩn",
            "body_html": "<p>Đã ẩn</p>",
            "status": "HIDDEN",
        },
    ).json()

    # PUB-01: Khách công khai chỉ thấy 3 bài PUBLISHED, không thấy DRAFT hay HIDDEN.
    pub_list = client.get("/api/public/contents")
    assert pub_list.status_code == 200
    slugs = [item["slug"] for item in pub_list.json()["items"]]
    assert slugs == ["dich-vu-transport", "dich-vu-rental", "dich-vu-warehouse"]
    assert client.get(f"/api/public/contents/{draft['slug']}").status_code == 404
    assert client.get(f"/api/public/contents/{hidden['slug']}").status_code == 404

    # Kiểm tra HTML đã được làm sạch XSS/script.
    pub_detail = client.get("/api/public/contents/dich-vu-transport").json()
    assert "<script" not in pub_detail["body_html"].lower()
    assert "javascript:" not in pub_detail["body_html"].lower()
    assert "onclick" not in pub_detail["body_html"].lower()
    assert 'href="https://example.com"' in pub_detail["body_html"]

    # PUB-05: Ẩn 2 trang dịch vụ đầu được phép, nhưng ẩn trang dịch vụ thứ 3 (làm ẩn cả 3 dịch vụ) bị chặn 409.
    for svc in services[:2]:
        r = client.post(
            f"/api/admin/cms/contents/{svc['code']}/status",
            headers=ADM,
            json={"version": svc["version"], "status": "HIDDEN", "reason": "Tạm ẩn"},
        )
        assert r.status_code == 200

    last_svc = services[2]
    blocked = client.post(
        f"/api/admin/cms/contents/{last_svc['code']}/status",
        headers=ADM,
        json={"version": last_svc["version"], "status": "HIDDEN", "reason": "Thử ẩn nốt dịch vụ thứ 3"},
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "ALL_SERVICES_HIDDEN"

    # Lịch sử revision chỉ thêm (append-only).
    with pytest.raises(sqlite3.IntegrityError, match="REVISION_APPEND_ONLY"):
        conn.execute("DELETE FROM public_content_revisions")


def test_public_request_submission_token_hash_xss_and_rate_limit(client, conn, clock, monkeypatch):
    # Chặn XSS/script trong form khách gửi.
    bad_xss = _transport_payload()
    bad_xss["contact"]["name"] = "<script>alert(1)</script>Công ty X"
    r_xss = client.post("/api/public/requests", json=bad_xss)
    assert r_xss.status_code == 422
    assert r_xss.json()["error"]["details"]["field"] == "name"

    # Gửi yêu cầu hợp lệ kèm Idempotency-Key: bấm lại trả cùng mã yêu cầu nhưng không lưu/trả lại token chữ rõ.
    payload = _transport_payload()
    headers = {"Idempotency-Key": "pub-req-idem-001"}
    first = client.post("/api/public/requests", headers=headers, json=payload)
    second = client.post("/api/public/requests", headers=headers, json=payload)
    assert first.status_code == 201, first.text
    assert second.status_code == 201
    data = first.json()
    assert data["request_code"] == second.json()["request_code"]
    assert second.json()["token"] is None and second.json()["lookup_token"] is None

    # Token >= 128 bit (32 ký tự URL-safe = 192 bit) và DB chỉ lưu SHA-256 hash.
    token = data["lookup_token"]
    assert len(token) >= 32
    row = conn.execute(
        "SELECT source, status, public_token_hash FROM service_requests WHERE code = ?",
        (data["request_code"],),
    ).fetchone()
    assert row["source"] == "WEBSITE"
    assert row["status"] == "NEW"
    assert row["public_token_hash"] == hashlib.sha256(token.encode("utf-8")).hexdigest()
    assert row["public_token_hash"] != token

    # Giới hạn 5 lần gửi / IP / 10 phút (đã gửi 1 lần ở trên, gửi thêm 4 lần nữa là đủ 5).
    for i in range(2, 6):
        r = client.post("/api/public/requests", json=payload)
        assert r.status_code == 201, f"Lần {i} phải thành công: {r.text}"

    # Kể cả khi kẻ tấn công giả mạo X-Forwarded-For, lần thứ 6 vẫn bị chặn 429.
    sixth = client.post(
        "/api/public/requests",
        headers={"X-Forwarded-For": "203.0.113.99"},
        json=payload,
    )
    assert sixth.status_code == 429
    assert sixth.json()["error"]["code"] == "RATE_LIMITED"

    # Khi đi qua reverse proxy tin cậy (TRUSTED_PROXIES), IP khác gửi được bình thường.
    monkeypatch.setenv("TRUSTED_PROXIES", "testclient,127.0.0.1")
    other_ip = client.post(
        "/api/public/requests",
        headers={"X-Forwarded-For": "203.0.113.99"},
        json=payload,
    )
    assert other_ip.status_code == 201
    monkeypatch.delenv("TRUSTED_PROXIES", raising=False)

    # Sau 11 phút, IP cũ gửi lại được.
    clock.advance(minutes=11)
    after_window = client.post("/api/public/requests", json=payload)
    assert after_window.status_code == 201


def test_public_lookup_post_only_br18_privacy_and_10_failed_attempts_rate_limit(client, conn, clock):
    sub = client.post(
        "/api/public/requests",
        headers={"X-Forwarded-For": "198.51.100.20"},
        json=_transport_payload(),
    ).json()
    req_code = sub["request_code"]
    token = sub["lookup_token"]

    # Chặn truyền token trên URL hoặc gọi GET /api/public/lookup.
    assert client.get("/api/public/lookup").status_code == 400
    assert client.get(f"/api/public/lookup?request_code={req_code}&token={token}").status_code == 400
    assert client.post(
        f"/api/public/lookup?token={token}",
        json={"request_code": req_code, "token": token},
    ).status_code == 400

    # Đưa yêu cầu qua báo giá -> tạo đơn -> nhận hàng -> giao hàng -> ghi chú nội bộ & công nợ.
    with write_tx(conn):
        cust = sales.create_customer(
            conn,
            customer_type="BUSINESS",
            name="Công ty TNHH Sao Mai",
            contact_name="Lê Hoàng Nam",
            phone="0903456789",
            email="nam@saomai.test",
            actor="sal1",
            now=clock(),
        )
        sales.update_request(
            conn,
            req_code,
            version=1,
            status="CLARIFYING",
            customer_code=cust["code"],
            assigned_to="sal1",
            clarification="Ghi chú làm rõ nội bộ tuyệt mật",
            actor="sal1",
            now=clock(),
        )
        quote = sales.create_quote(
            conn,
            request_code=req_code,
            valid_until="2026-12-05",
            service_data=_transport_payload()["service_data"],
            lines=[{"description": "Cước vận chuyển", "quantity": 1, "unit_price_vnd": 2_500_000}],
            actor="sal1",
            now=clock(),
        )
        sales.issue_quote(conn, quote["code"], actor="sal1", now=clock())
        accepted = sales.respond_quote_and_create_order(
            conn,
            quote["code"],
            accepted=True,
            channel="PHONE",
            content="Khách chốt báo giá",
            evidence=[],
            payment_due_date="2026-12-10",
            actor="sal1",
            now=clock(),
        )
        order = accepted["order"]
        sales.update_order_contact_note(
            conn,
            order["code"],
            contact_note="Ghi chú nội bộ: khách đang nợ 2.500.000 VND",
            version=order["version"],
            actor="sal1",
            now=clock(),
        )

    v = f.vehicle(conn, clock())
    d = f.driver(conn, clock())
    trip_code = order["operational_code"]
    with write_tx(conn):
        assignments.assign_trip(
            conn,
            trip_code,
            vehicle_id=v["id"],
            driver_id=d["id"],
            planned_start=f.vn(8),
            planned_end=f.vn(11),
            actor="dsp1",
            now=clock(),
        )
    clock.set(f.vn(8))
    with write_tx(conn):
        trip_ops.pickup(
            conn,
            trip_code,
            actual_at=f.vn(8),
            handed_over_by="Kho A",
            cargo_condition="Nguyên đai",
            discrepancy_note="Ghi chú nội bộ của tài xế",
            actor="dsp1",
            now=clock(),
        )

    # Tra cứu công khai bằng cả mã yêu cầu (REQ-...) lẫn mã đơn (ORD-...) với cùng token.
    lookup = client.post(
        "/api/public/lookup",
        json={"request_code": req_code, "token": token},
    )
    assert lookup.status_code == 200, lookup.text
    lookup_by_order = client.post(
        "/api/public/lookup",
        json={"request_code": order["code"], "token": token},
    )
    assert lookup_by_order.status_code == 200, lookup_by_order.text
    assert lookup_by_order.json() == lookup.json()
    body = lookup.json()
    assert body["request_code"] == req_code
    assert body["order_code"] == order["code"]
    assert body["order_status"] == "IN_PROGRESS"
    assert body["operational_status"] == "PICKED_UP"
    assert len(body["milestones"]) == 1
    assert body["milestones"][0]["milestone"] == "PICKED_UP"

    forbidden_fields = {
        "public_token_hash",
        "customer_code",
        "assigned_to",
        "contact_note",
        "events",
        "price_snapshot_vnd",
        "balance",
        "amount_due_vnd",
        "payments",
        "charges",
    }
    assert not (forbidden_fields & set(body.keys()))
    raw_json = lookup.text
    assert "tuyệt mật" not in raw_json
    assert "2500000" not in raw_json
    assert "Ghi chú nội bộ" not in raw_json

    # Nhập sai token 10 lần từ 1 IP -> lần thứ 11 bị chặn 429 RATE_LIMITED.
    for _ in range(10):
        wrong = client.post(
            "/api/public/lookup",
            headers={"X-Forwarded-For": "198.51.100.55"},
            json={"request_code": req_code, "token": "wrong-token-attempt"},
        )
        assert wrong.status_code == 401
        assert wrong.json()["error"]["code"] == "INVALID_LOOKUP_CREDENTIALS"

    eleventh = client.post(
        "/api/public/lookup",
        headers={"X-Forwarded-For": "198.51.100.55"},
        json={"request_code": req_code, "token": token},
    )
    assert eleventh.status_code == 429
    assert eleventh.json()["error"]["code"] == "RATE_LIMITED"
