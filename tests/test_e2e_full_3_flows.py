"""Kiểm thử 3 luồng đầu-cuối trọn vẹn (E2E-01, E2E-02, E2E-03) từ Website công khai
qua Xác thực phiên thật của 6 vai trò đến Hoàn tất vận hành, Thu tiền và Tra cứu tiến độ công khai (BE3).
"""

from backend import admin
from backend.db import write_tx
from tests import factories as f


_STAFF_ACCOUNTS = [
    ("adm1", "Quản trị", ["ADM"]),
    ("sal1", "Kinh doanh", ["SAL"]),
    ("dsp1", "Điều phối", ["DSP"]),
    ("whs1", "Thủ kho", ["WHS"]),
    ("mgr1", "Quản lý", ["MGR"]),
    ("fin1", "Kế toán", ["FIN"]),
]


def _login_all(client) -> dict[str, dict[str, str]]:
    headers_by_role: dict[str, dict[str, str]] = {}
    for uname, _, roles in _STAFF_ACCOUNTS:
        res = client.post("/api/auth/login", json={"username": uname, "password": "Password@123"})
        assert res.status_code == 200, res.text
        headers_by_role[roles[0]] = {"Authorization": f"Bearer {res.json()['token']}"}
    client.cookies.clear()
    return headers_by_role


def _setup_real_sessions(client, conn, now) -> dict[str, dict[str, str]]:
    """Tạo tài khoản thật trong bảng `users` cho các vai trò và đăng nhập lấy Bearer token."""
    with write_tx(conn):
        for uname, fname, roles in _STAFF_ACCOUNTS:
            admin.create_user(
                conn,
                username=uname,
                full_name=fname,
                password="Password@123",
                roles=roles,
                must_change_password=False,
                actor="seed",
                now=now,
            )
    return _login_all(client)


def _jump_clock(client, clock, target_dt) -> dict[str, dict[str, str]]:
    """Chuyển đồng hồ sang ca/mốc thời gian mới và làm mới phiên đăng nhập (do timeout phiên là 30 phút)."""
    clock.set(target_dt)
    return _login_all(client)


def test_e2e_01_transport_from_public_website_to_delivery_and_two_payments(client, conn, clock):
    h = _setup_real_sessions(client, conn, clock())

    # 1. Khách gửi yêu cầu vận tải trên website công khai (PUB-02, PUB-03).
    service_data = {
        "pickup_address": "Cảng Cát Lái, TP. Thủ Đức",
        "delivery_address": "KCN Tân Bình, TP. Hồ Chí Minh",
        "cargo_description": "Linh kiện máy đóng kiện, 1.200 kg",
        "cargo_weight_kg": 1200,
        "planned_pickup_at": f.iso(8),
        "committed_end": f.iso(12),
    }
    pub_sub = client.post(
        "/api/public/requests",
        headers={"Idempotency-Key": "e2e01-pub-submit", "X-Forwarded-For": "203.0.113.1"},
        json={
            "service_type": "TRANSPORT",
            "contact": {
                "name": "Công ty TNHH E2E Vận Tải",
                "contact_name": "Nguyễn Minh An",
                "phone": "0908111001",
                "email": "an@e2e-transport.test",
            },
            "service_data": service_data,
        },
    )
    assert pub_sub.status_code == 201, pub_sub.text
    req_code = pub_sub.json()["request_code"]
    lookup_token = pub_sub.json()["lookup_token"]

    # Khách tra cứu ngay sau khi gửi (PUB-04).
    lookup_initial = client.post(
        "/api/public/lookup",
        json={"request_code": req_code, "token": lookup_token},
    )
    assert lookup_initial.status_code == 200
    assert lookup_initial.json()["request_status"] == "NEW"
    assert lookup_initial.json()["order_code"] is None

    # 2. SAL đăng nhập thật, tạo khách hàng, gắn khách hàng & người phụ trách, lập và phát hành báo giá.
    cust = client.post(
        "/api/sales/customers",
        headers=h["SAL"],
        json={
            "customer_type": "BUSINESS",
            "name": "Công ty TNHH E2E Vận Tải",
            "contact_name": "Nguyễn Minh An",
            "phone": "0908111001",
            "email": "an@e2e-transport.test",
            "address": "TP. Hồ Chí Minh",
        },
    ).json()

    patched_req = client.patch(
        f"/api/sales/requests/{req_code}",
        headers=h["SAL"],
        json={
            "version": 1,
            "status": "CLARIFYING",
            "customer_code": cust["code"],
            "assigned_to": "sal1",
            "clarification": "Đã gọi xác nhận điểm lấy và tải trọng 1.200 kg.",
        },
    )
    assert patched_req.status_code == 200, patched_req.text

    quote = client.post(
        "/api/sales/quotes",
        headers=h["SAL"],
        json={
            "request_code": req_code,
            "valid_until": "2026-12-05",
            "service_data": service_data,
            "terms": "Thanh toán trong 10 ngày.",
            "lines": [{"description": "Cước vận chuyển chuyến 2 tấn", "quantity": 1, "unit_price_vnd": 2_000_000}],
        },
    ).json()
    assert client.post(f"/api/sales/quotes/{quote['code']}/issue", headers=h["SAL"]).status_code == 200

    # 3. Khách chấp thuận báo giá -> tạo đơn CONFIRMED và chuyến vận tải UNASSIGNED.
    accepted = client.post(
        f"/api/sales/quotes/{quote['code']}/response",
        headers={**h["SAL"], "Idempotency-Key": "e2e01-quote-accept"},
        json={
            "accepted": True,
            "channel": "PHONE",
            "content": "Khách đồng ý báo giá 2.000.000 VND.",
            "evidence": [],
            "payment_due_date": "2026-12-10",
        },
    )
    assert accepted.status_code == 200, accepted.text
    order_code = accepted.json()["order"]["code"]
    trip_code = accepted.json()["order"]["operational_code"]

    # 4. DSP tạo xe, tài xế, phân công, nhận hàng, vận chuyển, giao hàng có bằng chứng, hoàn tất chuyến.
    vehicle = client.post(
        "/api/vehicles",
        headers=h["DSP"],
        json={"plate_number": "51C-801.01", "vehicle_type": "TRUCK_2T", "capacity_kg": 2000},
    ).json()
    driver = client.post(
        "/api/drivers",
        headers=h["DSP"],
        json={"full_name": "Trần Văn Tài", "phone": "0909111001"},
    ).json()

    assert client.post(
        f"/api/trips/{trip_code}/assignment",
        headers={**h["DSP"], "Idempotency-Key": "e2e01-assign"},
        json={
            "vehicle_id": vehicle["id"],
            "driver_id": driver["id"],
            "planned_start": f.iso(8),
            "planned_end": f.iso(11),
        },
    ).status_code == 201

    h = _jump_clock(client, clock, f.vn(8))
    assert client.post(
        f"/api/trips/{trip_code}/pickup",
        headers={**h["DSP"], "Idempotency-Key": "e2e01-pickup"},
        json={
            "actual_at": f.iso(8),
            "handed_over_by": "Nguyễn Minh An",
            "cargo_condition": "Nguyên đai nguyên kiện",
            "actual_weight_kg": 1200,
        },
    ).status_code == 200

    h = _jump_clock(client, clock, f.vn(9))
    assert client.post(
        f"/api/trips/{trip_code}/in-transit",
        headers={**h["DSP"], "Idempotency-Key": "e2e01-transit"},
        json={"actual_at": f.iso(9)},
    ).status_code == 200

    pod_code = f.upload(client, headers=h["DSP"], name="pod.png", data=f.PNG)
    h = _jump_clock(client, clock, f.vn(10))
    assert client.post(
        f"/api/trips/{trip_code}/delivery",
        headers={**h["DSP"], "Idempotency-Key": "e2e01-deliver"},
        json={
            "actual_at": f.iso(10),
            "result": "SUCCESS",
            "receiver_name": "Lê Thị Bình",
            "note": "Giao đủ kiện",
            "attachment_codes": [pod_code],
        },
    ).status_code == 200

    clock.advance(minutes=15)
    assert client.post(
        f"/api/trips/{trip_code}/complete",
        headers={**h["DSP"], "Idempotency-Key": "e2e01-complete"},
        json={"released_at": f.iso(10, 15)},
    ).status_code == 200

    # 5. FIN thu tiền 2 đợt -> PARTIALLY_PAID -> PAID.
    p1 = client.post(
        f"/api/sales/orders/{order_code}/payments",
        headers={**h["FIN"], "Idempotency-Key": "e2e01-pay-1"},
        json={
            "amount_vnd": 1_000_000,
            "occurred_at": clock().isoformat(),
            "method": "BANK_TRANSFER",
            "reference": "UNC-E2E01-1",
        },
    )
    assert p1.status_code == 201
    assert p1.json()["balance"]["payment_status"] == "PARTIALLY_PAID"

    p2 = client.post(
        f"/api/sales/orders/{order_code}/payments",
        headers={**h["FIN"], "Idempotency-Key": "e2e01-pay-2"},
        json={
            "amount_vnd": 1_000_000,
            "occurred_at": clock().isoformat(),
            "method": "BANK_TRANSFER",
            "reference": "UNC-E2E01-2",
        },
    )
    assert p2.status_code == 201
    assert p2.json()["balance"]["payment_status"] == "PAID"
    assert p2.json()["balance"]["balance_vnd"] == 0

    # 6. Khách tra cứu lại công khai: thấy đủ 4 mốc, trạng thái COMPLETED, không lộ công nợ.
    lookup_final = client.post(
        "/api/public/lookup",
        json={"request_code": req_code, "token": lookup_token},
    )
    assert lookup_final.status_code == 200
    pub_body = lookup_final.json()
    assert pub_body["order_code"] == order_code
    assert pub_body["order_status"] == "COMPLETED"
    assert pub_body["operational_status"] == "COMPLETED"
    assert [m["milestone"] for m in pub_body["milestones"]] == [
        "PICKED_UP",
        "IN_TRANSIT",
        "DELIVERED",
        "COMPLETED",
    ]
    assert "balance" not in pub_body and "payments" not in pub_body


def test_e2e_02_rental_late_return_surcharge_approval_and_settlement(client, conn, clock):
    h = _setup_real_sessions(client, conn, clock())

    # 1. Khách gửi yêu cầu thuê xe trên website công khai.
    service_data = {
        "vehicle_type": "TRUCK_2T",
        "min_capacity_kg": 1500,
        "requested_start": f.iso(8),
        "requested_end": f.iso(18),
        "purpose": "Chở thiết bị công trình",
    }
    pub_sub = client.post(
        "/api/public/requests",
        headers={"X-Forwarded-For": "203.0.113.2"},
        json={
            "service_type": "RENTAL",
            "contact": {
                "name": "Công ty Thuê Xe E2E",
                "contact_name": "Lê Thị Hạnh",
                "phone": "0908222002",
                "email": "hanh@e2e-rental.test",
            },
            "service_data": service_data,
        },
    ).json()
    req_code, lookup_token = pub_sub["request_code"], pub_sub["lookup_token"]

    # 2. SAL lập khách hàng, báo giá 3.000.000 VND và xác nhận đơn.
    cust = client.post(
        "/api/sales/customers",
        headers=h["SAL"],
        json={
            "customer_type": "BUSINESS",
            "name": "Công ty Thuê Xe E2E",
            "contact_name": "Lê Thị Hạnh",
            "phone": "0908222002",
            "email": "hanh@e2e-rental.test",
        },
    ).json()
    client.patch(
        f"/api/sales/requests/{req_code}",
        headers=h["SAL"],
        json={"version": 1, "customer_code": cust["code"], "assigned_to": "sal1"},
    )
    quote = client.post(
        "/api/sales/quotes",
        headers=h["SAL"],
        json={
            "request_code": req_code,
            "valid_until": "2026-12-05",
            "service_data": service_data,
            "lines": [{"description": "Thuê xe tải 2 tấn", "quantity": 1, "unit_price_vnd": 3_000_000}],
        },
    ).json()
    client.post(f"/api/sales/quotes/{quote['code']}/issue", headers=h["SAL"])
    accepted = client.post(
        f"/api/sales/quotes/{quote['code']}/response",
        headers={**h["SAL"], "Idempotency-Key": "e2e02-accept"},
        json={
            "accepted": True,
            "channel": "PHONE",
            "content": "Khách xác nhận thuê xe.",
            "evidence": [],
            "payment_due_date": "2026-12-10",
        },
    ).json()
    order_code = accepted["order"]["code"]
    rental_code = accepted["order"]["operational_code"]

    # 3. DSP giữ xe, bàn giao xe, nhận lại xe trả muộn hôm sau.
    vehicle = client.post(
        "/api/vehicles",
        headers=h["DSP"],
        json={"plate_number": "51C-802.01", "vehicle_type": "TRUCK_2T", "capacity_kg": 2000},
    ).json()
    assert client.post(
        f"/api/rentals/{rental_code}/reservation",
        headers={**h["DSP"], "Idempotency-Key": "e2e02-reserve"},
        json={"vehicle_id": vehicle["id"]},
    ).status_code == 201

    handover_att = f.upload(client, headers=h["DSP"])
    h = _jump_clock(client, clock, f.vn(8))
    assert client.post(
        f"/api/rentals/{rental_code}/handover",
        headers={**h["DSP"], "Idempotency-Key": "e2e02-handover"},
        json={
            "actual_at": f.iso(8),
            "receiver_name": "Lê Thị Hạnh",
            "odometer_km": 50_000,
            "body_condition": "Tốt",
            "fuel_level": "Đầy bình",
            "accessories": "Đủ phụ kiện",
            "attachment_codes": [handover_att],
        },
    ).status_code == 200

    return_att = f.upload(client, headers=h["DSP"])
    h = _jump_clock(client, clock, f.vn(19, day=2))
    returned = client.post(
        f"/api/rentals/{rental_code}/return",
        headers={**h["DSP"], "Idempotency-Key": "e2e02-return"},
        json={
            "actual_at": f.iso(19, day=2),
            "returned_by": "Lê Thị Hạnh",
            "odometer_km": 50_180,
            "body_condition": "Tốt",
            "fuel_level": "Nửa bình",
            "accessories": "Đủ phụ kiện",
            "attachment_codes": [return_att],
        },
    )
    assert returned.status_code == 200
    assert returned.json()["late_minutes"] == 1500

    # 4. DSP đề nghị phụ phí trả xe muộn 300.000 VND; khi chưa duyệt thì chặn hoàn tất hồ sơ thuê.
    charge_att = f.upload(client, headers=h["DSP"])
    charge = client.post(
        f"/api/sales/orders/{order_code}/charges",
        headers={**h["DSP"], "Idempotency-Key": "e2e02-charge"},
        json={"amount_vnd": 300_000, "reason": "Phụ phí trả xe muộn", "evidence": [charge_att]},
    ).json()

    blocked_complete = client.post(f"/api/rentals/{rental_code}/complete", headers=h["DSP"])
    assert blocked_complete.status_code == 409
    assert blocked_complete.json()["error"]["code"] == "PENDING_CHARGES"

    # 5. MGR duyệt phụ phí -> DSP hoàn tất hồ sơ thuê -> FIN thu đủ 3.300.000 VND.
    assert client.post(
        f"/api/sales/charges/{charge['code']}/decision",
        headers={**h["MGR"], "Idempotency-Key": "e2e02-charge-approve"},
        json={"approved": True, "decision_note": "Đồng ý theo biên bản trả xe"},
    ).status_code == 200

    assert client.post(f"/api/rentals/{rental_code}/complete", headers=h["DSP"]).status_code == 200

    paid = client.post(
        f"/api/sales/orders/{order_code}/payments",
        headers={**h["FIN"], "Idempotency-Key": "e2e02-pay"},
        json={
            "amount_vnd": 3_300_000,
            "occurred_at": clock().isoformat(),
            "method": "BANK_TRANSFER",
            "reference": "UNC-E2E02-1",
        },
    )
    assert paid.status_code == 201
    assert paid.json()["balance"]["payment_status"] == "PAID"

    # 6. Khách tra cứu công khai.
    pub_lookup = client.post(
        "/api/public/lookup",
        json={"request_code": req_code, "token": lookup_token},
    ).json()
    assert pub_lookup["order_status"] == "COMPLETED"
    assert pub_lookup["operational_status"] == "COMPLETED"
    assert [m["milestone"] for m in pub_lookup["milestones"]] == ["HANDOVER", "RETURN"]


def test_e2e_03_warehouse_partial_issues_linked_to_transport_and_separate_receivables(client, conn, clock):
    h = _setup_real_sessions(client, conn, clock())

    # 1. Khách gửi yêu cầu kho trên website công khai.
    wh_data = {
        "cargo_description": "Động cơ điện đóng kiện",
        "expected_in_at": f.iso(7),
        "expected_out_at": f.iso(18),
        "storage_notes": "Để nơi khô ráo",
        "lines": [{"item_code": "MOTOR-01", "item_name": "Động cơ điện", "unit_code": "KIEN", "expected_qty": 100}],
    }
    wh_pub = client.post(
        "/api/public/requests",
        headers={"X-Forwarded-For": "203.0.113.3"},
        json={
            "service_type": "WAREHOUSE",
            "contact": {
                "name": "Công ty Kho Vận E2E",
                "contact_name": "Phạm Văn Nam",
                "phone": "0908333003",
                "email": "nam@e2e-warehouse.test",
            },
            "service_data": wh_data,
        },
    ).json()
    wh_req_code, wh_token = wh_pub["request_code"], wh_pub["lookup_token"]

    cust = client.post(
        "/api/sales/customers",
        headers=h["SAL"],
        json={
            "customer_type": "BUSINESS",
            "name": "Công ty Kho Vận E2E",
            "contact_name": "Phạm Văn Nam",
            "phone": "0908333003",
            "email": "nam@e2e-warehouse.test",
        },
    ).json()

    client.patch(
        f"/api/sales/requests/{wh_req_code}",
        headers=h["SAL"],
        json={"version": 1, "customer_code": cust["code"], "assigned_to": "sal1"},
    )
    wh_quote = client.post(
        "/api/sales/quotes",
        headers=h["SAL"],
        json={
            "request_code": wh_req_code,
            "valid_until": "2026-12-05",
            "service_data": wh_data,
            "lines": [{"description": "Lưu kho trung chuyển 100 kiện", "quantity": 1, "unit_price_vnd": 500_000}],
        },
    ).json()
    client.post(f"/api/sales/quotes/{wh_quote['code']}/issue", headers=h["SAL"])
    wh_order = client.post(
        f"/api/sales/quotes/{wh_quote['code']}/response",
        headers={**h["SAL"], "Idempotency-Key": "e2e03-wh-accept"},
        json={
            "accepted": True,
            "channel": "PHONE",
            "content": "Đồng ý đơn kho",
            "evidence": [],
            "payment_due_date": "2026-12-10",
        },
    ).json()["order"]
    lot_code = wh_order["operational_code"]
    lot_detail = client.get(f"/api/warehouse/lots/{lot_code}", headers=h["WHS"]).json()
    lot_line_id = lot_detail["lines"][0]["id"]

    # 2. WHS nhập kho 2 đợt (60 + 40 = 100 kiện).
    for idx, qty in enumerate((60, 40), start=1):
        rec = client.post(
            f"/api/warehouse/lots/{lot_code}/receipts",
            headers={**h["WHS"], "Idempotency-Key": f"e2e03-rec-{idx}"},
            json={
                "occurred_at": clock().isoformat(),
                "counterpart_name": "Tài xế giao hàng",
                "lines": [{"lot_line_id": lot_line_id, "quantity": qty}],
            },
        ).json()
        assert client.post(
            f"/api/warehouse/documents/{rec['code']}/post",
            headers={**h["WHS"], "Idempotency-Key": f"e2e03-rec-post-{idx}"},
        ).status_code == 200

    # 3. Khách gửi yêu cầu vận tải lấy hàng từ kho trên website công khai.
    tr_data = {
        "pickup_address": "Kho trung chuyển của công ty",
        "delivery_address": "Nhà máy Thủ Đức",
        "cargo_description": "70 kiện động cơ điện xuất từ kho",
        "cargo_weight_kg": 900,
        "planned_pickup_at": f.iso(8),
        "committed_end": f.iso(12),
        "pickup_from_warehouse": True,
    }
    tr_pub = client.post(
        "/api/public/requests",
        headers={"X-Forwarded-For": "203.0.113.3"},
        json={
            "service_type": "TRANSPORT",
            "contact": {
                "name": "Công ty Kho Vận E2E",
                "contact_name": "Phạm Văn Nam",
                "phone": "0908333003",
                "email": "nam@e2e-warehouse.test",
            },
            "service_data": tr_data,
        },
    ).json()
    tr_req_code, tr_token = tr_pub["request_code"], tr_pub["lookup_token"]

    client.patch(
        f"/api/sales/requests/{tr_req_code}",
        headers=h["SAL"],
        json={"version": 1, "customer_code": cust["code"], "assigned_to": "sal1"},
    )
    tr_quote = client.post(
        "/api/sales/quotes",
        headers=h["SAL"],
        json={
            "request_code": tr_req_code,
            "valid_until": "2026-12-05",
            "service_data": tr_data,
            "lines": [{"description": "Vận chuyển 70 kiện từ kho đi Thủ Đức", "quantity": 1, "unit_price_vnd": 800_000}],
        },
    ).json()
    client.post(f"/api/sales/quotes/{tr_quote['code']}/issue", headers=h["SAL"])
    tr_order = client.post(
        f"/api/sales/quotes/{tr_quote['code']}/response",
        headers={**h["SAL"], "Idempotency-Key": "e2e03-tr-accept"},
        json={
            "accepted": True,
            "channel": "PHONE",
            "content": "Đồng ý đơn vận tải nối kho",
            "evidence": [],
            "payment_due_date": "2026-12-10",
        },
    ).json()["order"]
    trip_code = tr_order["operational_code"]

    # 4. WHS xuất từng phần: 30 kiện trực tiếp + 70 kiện liên kết với chuyến vận tải trip_code, rồi đóng lô.
    iss1 = client.post(
        f"/api/warehouse/lots/{lot_code}/issues",
        headers={**h["WHS"], "Idempotency-Key": "e2e03-iss-1"},
        json={
            "occurred_at": clock().isoformat(),
            "counterpart_name": "Nhân viên nhận tại kho",
            "destination": "Khách tự lấy 30 kiện",
            "lines": [{"lot_line_id": lot_line_id, "quantity": 30}],
        },
    ).json()
    assert client.post(
        f"/api/warehouse/documents/{iss1['code']}/post",
        headers={**h["WHS"], "Idempotency-Key": "e2e03-iss-post-1"},
    ).status_code == 200

    iss2 = client.post(
        f"/api/warehouse/lots/{lot_code}/issues",
        headers={**h["WHS"], "Idempotency-Key": "e2e03-iss-2"},
        json={
            "occurred_at": clock().isoformat(),
            "counterpart_name": "Tài xế chuyến vận tải",
            "destination": "Nhà máy Thủ Đức",
            "transport_trip_code": trip_code,
            "lines": [{"lot_line_id": lot_line_id, "quantity": 70}],
        },
    ).json()
    assert client.post(
        f"/api/warehouse/documents/{iss2['code']}/post",
        headers={**h["WHS"], "Idempotency-Key": "e2e03-iss-post-2"},
    ).status_code == 200

    curr_lot = client.get(f"/api/warehouse/lots/{lot_code}", headers=h["WHS"]).json()
    assert curr_lot["lines"][0]["on_hand"] == 0
    closed_lot = client.post(
        f"/api/warehouse/lots/{lot_code}/close",
        headers=h["WHS"],
        json={"version": curr_lot["version"], "no_more_receipts": True},
    )
    assert closed_lot.status_code == 200

    # 5. DSP thực hiện và hoàn tất chuyến vận tải trip_code.
    vehicle = client.post(
        "/api/vehicles",
        headers=h["DSP"],
        json={"plate_number": "51C-803.01", "vehicle_type": "TRUCK_2T", "capacity_kg": 2000},
    ).json()
    driver = client.post(
        "/api/drivers",
        headers=h["DSP"],
        json={"full_name": "Lê Văn Tài", "phone": "0909333003"},
    ).json()
    client.post(
        f"/api/trips/{trip_code}/assignment",
        headers=h["DSP"],
        json={"vehicle_id": vehicle["id"], "driver_id": driver["id"], "planned_start": f.iso(8), "planned_end": f.iso(10)},
    )
    h = _jump_clock(client, clock, f.vn(8))
    client.post(
        f"/api/trips/{trip_code}/pickup",
        headers=h["DSP"],
        json={"actual_at": f.iso(8), "handed_over_by": "Thủ kho", "cargo_condition": "Đủ 70 kiện", "actual_weight_kg": 900},
    )
    h = _jump_clock(client, clock, f.vn(9))
    client.post(f"/api/trips/{trip_code}/in-transit", headers=h["DSP"], json={"actual_at": f.iso(9)})
    pod = f.upload(client, headers=h["DSP"])
    h = _jump_clock(client, clock, f.vn(10))
    client.post(
        f"/api/trips/{trip_code}/delivery",
        headers=h["DSP"],
        json={"actual_at": f.iso(10), "result": "SUCCESS", "receiver_name": "Quản đốc Hùng", "attachment_codes": [pod]},
    )
    client.post(f"/api/trips/{trip_code}/complete", headers=h["DSP"], json={"released_at": f.iso(10)})

    # 6. FIN đối chiếu và thu tiền riêng từng đơn (đơn kho 500k, đơn vận tải 800k).
    wh_rec = client.get(f"/api/sales/orders/{wh_order['code']}/receivables", headers=h["FIN"]).json()
    tr_rec = client.get(f"/api/sales/orders/{tr_order['code']}/receivables", headers=h["FIN"]).json()
    assert wh_rec["balance_vnd"] == 500_000
    assert tr_rec["balance_vnd"] == 800_000

    assert client.post(
        f"/api/sales/orders/{wh_order['code']}/payments",
        headers=h["FIN"],
        json={"amount_vnd": 500_000, "occurred_at": clock().isoformat(), "method": "CASH"},
    ).json()["balance"]["payment_status"] == "PAID"

    assert client.post(
        f"/api/sales/orders/{tr_order['code']}/payments",
        headers=h["FIN"],
        json={"amount_vnd": 800_000, "occurred_at": clock().isoformat(), "method": "CASH"},
    ).json()["balance"]["payment_status"] == "PAID"

    # 7. Khách tra cứu công khai cả 2 mã đều COMPLETED.
    wh_lookup = client.post("/api/public/lookup", json={"request_code": wh_req_code, "token": wh_token}).json()
    tr_lookup = client.post("/api/public/lookup", json={"request_code": tr_req_code, "token": tr_token}).json()
    assert wh_lookup["order_status"] == "COMPLETED"
    assert tr_lookup["order_status"] == "COMPLETED"
