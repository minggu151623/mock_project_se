"""Bộ test hồi quy theo kết quả review BE3 của Minh (6 mục review)."""

from backend import sales
from backend.db import write_tx
from tests.factories import ADM, MGR, SAL
from tests.test_be3_auth_admin import _seed_admin_and_staff
from tests.test_be3_public_cms import _transport_payload


def test_lookup_limit_not_bypassed_with_forwarded_for(client, conn, clock):
    code = client.post("/api/public/requests", json=_transport_payload()).json()["request_code"]
    statuses = [
        client.post(
            "/api/public/lookup",
            headers={"X-Forwarded-For": f"10.0.0.{i}"},
            json={"request_code": code, "token": "wrong"},
        ).status_code
        for i in range(15)
    ]
    assert 429 in statuses


def test_idempotency_does_not_store_plain_token(client, conn, clock):
    r = client.post(
        "/api/public/requests",
        headers={"Idempotency-Key": "k-1"},
        json=_transport_payload(),
    )
    assert r.status_code == 201
    token = r.json()["token"]
    assert token and len(token) >= 32
    hits = conn.execute(
        "SELECT COUNT(*) FROM idempotency_keys WHERE response_json LIKE ?",
        (f"%{token}%",),
    ).fetchone()[0]
    assert hits == 0


def test_login_error_same_for_existing_and_missing_user(client, conn, clock):
    _seed_admin_and_staff(conn, clock())
    exists = client.post("/api/auth/login", json={"username": "sal1", "password": "WrongPassword!"})
    missing = client.post("/api/auth/login", json={"username": "nobody", "password": "WrongPassword!"})
    assert exists.status_code == missing.status_code
    assert exists.json() == missing.json()


def test_submit_requires_data_use_acknowledgement(client, conn, clock):
    payload = _transport_payload()
    payload.pop("data_use_acknowledged", None)
    r = client.post("/api/public/requests", json=payload)
    assert r.status_code == 422
    assert r.json()["error"]["details"]["field"] == "data_use_acknowledged"

    payload_false = {**_transport_payload(), "data_use_acknowledged": False}
    r_false = client.post("/api/public/requests", json=payload_false)
    assert r_false.status_code == 422
    assert r_false.json()["error"]["details"]["field"] == "data_use_acknowledged"


def test_lookup_accepts_order_code_as_well_as_request_code(client, conn, clock):
    sub = client.post("/api/public/requests", json=_transport_payload()).json()
    req_code, token = sub["request_code"], sub["lookup_token"]

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
            customer_code=cust["code"],
            actor="sal1",
            now=clock(),
        )
        quote = sales.create_quote(
            conn,
            request_code=req_code,
            valid_until="2026-12-05",
            service_data=_transport_payload()["service_data"],
            lines=[{"description": "Cước vận chuyển", "quantity": 1, "unit_price_vnd": 1_500_000}],
            actor="sal1",
            now=clock(),
        )
        sales.issue_quote(conn, quote["code"], actor="sal1", now=clock())
        order = sales.respond_quote_and_create_order(
            conn,
            quote["code"],
            accepted=True,
            channel="PHONE",
            content="Chấp thuận",
            evidence=[],
            payment_due_date="2026-12-10",
            actor="sal1",
            now=clock(),
        )["order"]

    by_req = client.post("/api/public/lookup", json={"request_code": req_code, "token": token})
    by_ord = client.post("/api/public/lookup", json={"request_code": order["code"], "token": token})
    assert by_req.status_code == 200
    assert by_ord.status_code == 200
    assert by_req.json() == by_ord.json()
    assert by_ord.json()["order_code"] == order["code"]


def test_cms_write_restricted_to_adm_only(client):
    body = {
        "slug": "trang-thu-quyen-cms",
        "content_type": "COMPANY",
        "title": "Trang thử quyền",
        "body_html": "<p>Nội dung</p>",
        "status": "DRAFT",
    }
    assert client.post("/api/admin/cms/contents", headers=SAL, json=body).status_code == 403
    assert client.post("/api/admin/cms/contents", headers=MGR, json=body).status_code == 403
    assert client.post("/api/admin/cms/contents", headers=ADM, json=body).status_code == 201
