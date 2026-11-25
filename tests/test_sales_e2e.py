"""BE1 E2E-01 business slice from sales intake to a reconciled receivable."""

from backend import assignments, rental_ops, sales, trip_ops, warehouse
from backend.db import write_tx
from tests import factories as f


def accepted_order(
    conn,
    *,
    customer,
    service_type,
    service_data,
    amount_vnd,
    description,
    now,
):
    """Create one accepted service order through the BE1 business services."""
    with write_tx(conn):
        request = sales.create_request(
            conn,
            service_type=service_type,
            source="INTERNAL",
            contact={
                "name": customer["name"],
                "contact_name": customer["contact_name"],
                "phone": customer["phone"],
                "email": customer["email"],
            },
            service_data=service_data,
            actor="sal1",
            now=now,
        )
        request = sales.update_request(
            conn,
            request["code"],
            version=request["version"],
            customer_code=customer["code"],
            actor="sal1",
            now=now,
        )
        quote = sales.create_quote(
            conn,
            request_code=request["code"],
            valid_until="2026-12-05",
            service_data=service_data,
            lines=[
                {
                    "description": description,
                    "quantity": 1,
                    "unit_price_vnd": amount_vnd,
                }
            ],
            actor="sal1",
            now=now,
        )
        sales.issue_quote(conn, quote["code"], actor="sal1", now=now)
        result = sales.respond_quote_and_create_order(
            conn,
            quote["code"],
            accepted=True,
            channel="PHONE",
            content="Khách xác nhận phương án dịch vụ.",
            evidence=[],
            payment_due_date="2026-12-10",
            actor="sal1",
            now=now,
        )
    return result["order"]


def test_transport_request_quote_order_operation_and_two_payments(client, conn, clock):
    customer = client.post(
        "/api/sales/customers",
        headers=f.SAL,
        json={
            "customer_type": "BUSINESS",
            "name": "Công ty E2E",
            "contact_name": "Nguyễn Minh An",
            "phone": "0908777666",
            "email": "e2e@example.test",
            "address": "TP. Hồ Chí Minh",
        },
    )
    assert customer.status_code == 201, customer.text

    service_data = {
        "pickup_address": "Cảng Cát Lái",
        "delivery_address": "Khu công nghiệp Tân Bình",
        "cargo_description": "Linh kiện máy, 1.200 kg",
        "cargo_weight_kg": 1200,
        "planned_pickup_at": f.iso(8),
        "committed_end": f.iso(12),
    }
    request = client.post(
        "/api/sales/requests",
        headers=f.SAL,
        json={
            "service_type": "TRANSPORT",
            "contact": {
                "name": "Công ty E2E",
                "contact_name": "Nguyễn Minh An",
                "phone": "0908777666",
                "email": "e2e@example.test",
            },
            "service_data": service_data,
        },
    )
    assert request.status_code == 201, request.text
    request_code = request.json()["code"]
    attached = client.patch(
        f"/api/sales/requests/{request_code}",
        headers=f.SAL,
        json={"version": 1, "customer_code": customer.json()["code"]},
    )
    assert attached.status_code == 200, attached.text

    quote = client.post(
        "/api/sales/quotes",
        headers=f.SAL,
        json={
            "request_code": request_code,
            "valid_until": "2026-12-05",
            "service_data": service_data,
            "terms": "Thanh toán theo tiến độ.",
            "lines": [
                {
                    "description": "Cước vận chuyển",
                    "quantity": 1,
                    "unit_price_vnd": 2_000_000,
                }
            ],
        },
    )
    assert quote.status_code == 201, quote.text
    quote_code = quote.json()["code"]
    issued = client.post(f"/api/sales/quotes/{quote_code}/issue", headers=f.SAL)
    assert issued.status_code == 200, issued.text

    accepted = client.post(
        f"/api/sales/quotes/{quote_code}/response",
        headers={**f.SAL, "Idempotency-Key": "e2e-01-accept"},
        json={
            "accepted": True,
            "channel": "PHONE",
            "content": "Khách xác nhận gói vận chuyển 2.000.000 VND.",
            "evidence": [],
            "payment_due_date": "2026-12-10",
        },
    )
    assert accepted.status_code == 200, accepted.text
    order_code = accepted.json()["order"]["code"]
    operational_code = accepted.json()["order"]["operational_code"]

    vehicle = f.vehicle(conn, clock())
    driver = f.driver(conn, clock())
    with write_tx(conn):
        assignments.assign_trip(
            conn,
            operational_code,
            vehicle_id=vehicle["id"],
            driver_id=driver["id"],
            planned_start=f.vn(8),
            planned_end=f.vn(10),
            actor="dsp1",
            now=clock(),
        )

    clock.set(f.vn(8))
    with write_tx(conn):
        trip_ops.pickup(
            conn,
            operational_code,
            actual_at=f.vn(8),
            handed_over_by="Nguyễn Minh An",
            cargo_condition="Hàng nguyên kiện.",
            actor="dsp1",
            now=clock(),
            actual_weight_kg=1200,
        )
    clock.set(f.vn(9))
    with write_tx(conn):
        trip_ops.start_transit(
            conn, operational_code, actual_at=f.vn(9), actor="dsp1", now=clock()
        )
    delivery_evidence = f.upload(client, headers=f.DSP)
    clock.set(f.vn(10))
    with write_tx(conn):
        trip_ops.deliver(
            conn,
            operational_code,
            actual_at=f.vn(10),
            result="SUCCESS",
            receiver_name="Trần Thị Bình",
            note="Đã giao đủ hàng.",
            attachment_codes=[delivery_evidence],
            actor="dsp1",
            now=clock(),
        )
        trip_ops.complete(
            conn,
            operational_code,
            released_at=f.vn(10),
            actor="dsp1",
            now=clock(),
        )

    completed_before_payment = client.get(
        f"/api/sales/orders/{order_code}", headers=f.FIN
    ).json()
    assert completed_before_payment["status"] == "COMPLETED"
    assert completed_before_payment["balance"]["payment_status"] == "UNPAID"
    assert completed_before_payment["operational_record"]["status"] == "COMPLETED"

    for number in (1, 2):
        payment = client.post(
            f"/api/sales/orders/{order_code}/payments",
            headers={**f.FIN, "Idempotency-Key": f"e2e-01-payment-{number}"},
            json={
                "amount_vnd": 1_000_000,
                "occurred_at": clock().isoformat(),
                "method": "BANK_TRANSFER",
                "reference": f"E2E-01-TRANSFER-{number}",
                "note": "Thanh toán theo kịch bản E2E.",
            },
        )
        assert payment.status_code == 201, payment.text

    detail = client.get(f"/api/sales/orders/{order_code}", headers=f.FIN)
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["status"] == "COMPLETED"
    assert body["operational_record"]["status"] == "COMPLETED"
    assert body["balance"]["amount_paid_vnd"] == 2_000_000
    assert body["balance"]["balance_vnd"] == 0
    assert body["balance"]["payment_status"] == "PAID"
    assert len(body["payments"]) == 2

    request_detail = client.get(
        f"/api/sales/requests/{request_code}", headers=f.SAL
    ).json()
    event_types = [event["event_type"] for event in request_detail["events"]]
    assert event_types.count("STATUS_CHANGED") == 2
    assert "public_token_hash" not in request_detail


def test_finance_api_rejects_non_finance_role(client, conn, clock):
    from tests.test_sales import create_order

    _, _, order = create_order(conn, clock())
    response = client.post(
        f"/api/sales/orders/{order['code']}/payments",
        headers={**f.SAL, "Idempotency-Key": "wrong-role-payment"},
        json={
            "amount_vnd": 1,
            "occurred_at": clock().isoformat(),
            "method": "CASH",
            "reference": None,
            "note": "Không được phép.",
        },
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_rental_late_return_charge_completion_and_payment(client, conn, clock):
    with write_tx(conn):
        customer = sales.create_customer(
            conn,
            customer_type="BUSINESS",
            name="Công ty thuê xe E2E",
            contact_name="Lê Thị Hạnh",
            phone="0908666555",
            email="rental-e2e@example.test",
            actor="sal1",
            now=clock(),
        )
        service_data = {
            "vehicle_type": "TRUCK_2T",
            "min_capacity_kg": 1500,
            "requested_start": f.iso(8),
            "requested_end": f.iso(18),
            "purpose": "Giao thiết bị cho công trình.",
        }
        request = sales.create_request(
            conn,
            service_type="RENTAL",
            source="INTERNAL",
            contact={
                "name": customer["name"],
                "contact_name": customer["contact_name"],
                "phone": customer["phone"],
                "email": customer["email"],
            },
            service_data=service_data,
            actor="sal1",
            now=clock(),
        )
        request = sales.update_request(
            conn,
            request["code"],
            version=request["version"],
            customer_code=customer["code"],
            actor="sal1",
            now=clock(),
        )
        quote = sales.create_quote(
            conn,
            request_code=request["code"],
            valid_until="2026-12-05",
            service_data=service_data,
            lines=[
                {
                    "description": "Thuê xe tải 2 tấn",
                    "quantity": 1,
                    "unit_price_vnd": 3_000_000,
                }
            ],
            actor="sal1",
            now=clock(),
        )
        sales.issue_quote(conn, quote["code"], actor="sal1", now=clock())
        accepted = sales.respond_quote_and_create_order(
            conn,
            quote["code"],
            accepted=True,
            channel="PHONE",
            content="Khách xác nhận thuê xe.",
            evidence=[],
            payment_due_date="2026-12-10",
            actor="sal1",
            now=clock(),
        )
        order = accepted["order"]

    vehicle = f.vehicle(conn, clock(), plate="51C-900.01", vtype="TRUCK_2T")
    rental_code = order["operational_code"]
    with write_tx(conn):
        assignments.reserve_rental(
            conn, rental_code, vehicle_id=vehicle["id"], actor="dsp1", now=clock()
        )

    handover_evidence = f.upload(client, headers=f.DSP)
    clock.set(f.vn(8))
    with write_tx(conn):
        rental_ops.handover(
            conn,
            rental_code,
            actual_at=f.vn(8),
            receiver_name="Lê Thị Hạnh",
            odometer_km=50_000,
            body_condition="Thân xe tốt.",
            fuel_level="Đầy bình.",
            accessories="Đủ phụ kiện.",
            attachment_codes=[handover_evidence],
            actor="dsp1",
            now=clock(),
        )

    return_evidence = f.upload(client, headers=f.DSP)
    clock.set(f.vn(19, day=2))
    with write_tx(conn):
        returned = rental_ops.return_vehicle(
            conn,
            rental_code,
            actual_at=f.vn(19, day=2),
            returned_by="Lê Thị Hạnh",
            odometer_km=50_180,
            body_condition="Thân xe tốt.",
            fuel_level="Còn nửa bình.",
            accessories="Đủ phụ kiện.",
            attachment_codes=[return_evidence],
            actor="dsp1",
            now=clock(),
        )
    assert returned["late_minutes"] == 1500

    charge_evidence = f.upload(client, headers=f.DSP)
    with write_tx(conn):
        charge = sales.propose_charge(
            conn,
            order["code"],
            amount_vnd=300_000,
            reason="Trả xe muộn theo biên bản.",
            evidence=[charge_evidence],
            roles=frozenset({"DSP"}),
            actor="dsp1",
            now=clock(),
        )
        assert sales.order_balance(conn, order["code"])["amount_due_vnd"] == 3_000_000
        decision = sales.decide_charge(
            conn,
            charge["code"],
            approved=True,
            decision_note="Đối chiếu thời gian trả xe.",
            actor="mgr1",
            now=clock(),
        )
        assert decision["status"] == "APPROVED"
        rental_ops.complete_rental(conn, rental_code, actor="dsp1", now=clock())

    before_payment = sales.order_balance(conn, order["code"])
    assert before_payment["order_status"] == "COMPLETED"
    assert before_payment["amount_due_vnd"] == 3_300_000
    assert before_payment["payment_status"] == "UNPAID"

    with write_tx(conn):
        payment = sales.record_payment(
            conn,
            order["code"],
            amount_vnd=3_300_000,
            occurred_at=clock(),
            method="BANK_TRANSFER",
            reference="RENTAL-E2E-001",
            note="Thanh toán tiền thuê và phụ phí.",
            actor="fin1",
            now=clock(),
        )
    assert payment["balance"]["payment_status"] == "PAID"
    assert payment["balance"]["balance_vnd"] == 0


def test_warehouse_to_transport_orders_keep_separate_receivables(client, conn, clock):
    with write_tx(conn):
        customer = sales.create_customer(
            conn,
            customer_type="BUSINESS",
            name="Công ty kho E2E",
            contact_name="Phạm Văn Nam",
            phone="0908555444",
            email="warehouse-e2e@example.test",
            actor="sal1",
            now=clock(),
        )

    warehouse_data = {
        "cargo_description": "Linh kiện đóng kiện",
        "expected_in_at": f.iso(7),
        "expected_out_at": f.iso(18),
        "storage_notes": "Giữ nơi khô ráo.",
        "lines": [
            {
                "item_code": "MOTOR-01",
                "item_name": "Động cơ điện",
                "unit_code": "KIEN",
                "expected_qty": 100,
            }
        ],
    }
    warehouse_order = accepted_order(
        conn,
        customer=customer,
        service_type="WAREHOUSE",
        service_data=warehouse_data,
        amount_vnd=500_000,
        description="Dịch vụ lưu kho 100 kiện",
        now=clock(),
    )
    lot_code = warehouse_order["operational_code"]
    lot = warehouse.get_lot(conn, lot_code, clock())
    lot_line_id = lot["lines"][0]["id"]

    for quantity in (60, 40):
        with write_tx(conn):
            receipt = warehouse.create_receipt(
                conn,
                lot_code,
                occurred_at=clock(),
                counterpart_name="Đội giao hàng",
                lines=[{"lot_line_id": lot_line_id, "quantity": quantity}],
                actor="whs1",
                now=clock(),
            )
            warehouse.post_document(conn, receipt["code"], actor="whs1", now=clock())

    transport_data = {
        "pickup_address": "Kho trung chuyển của công ty",
        "delivery_address": "Công trình tại Thủ Đức",
        "cargo_description": "70 kiện động cơ điện",
        "cargo_weight_kg": 900,
        "planned_pickup_at": f.iso(8),
        "committed_end": f.iso(12),
        "pickup_from_warehouse": True,
    }
    transport_order = accepted_order(
        conn,
        customer=customer,
        service_type="TRANSPORT",
        service_data=transport_data,
        amount_vnd=800_000,
        description="Vận chuyển 70 kiện từ kho",
        now=clock(),
    )
    trip_code = transport_order["operational_code"]

    with write_tx(conn):
        first_issue = warehouse.create_issue(
            conn,
            lot_code,
            occurred_at=clock(),
            counterpart_name="Điều phối kho",
            destination="Khu vực chờ lấy hàng",
            lines=[{"lot_line_id": lot_line_id, "quantity": 30}],
            actor="whs1",
            now=clock(),
        )
        warehouse.post_document(conn, first_issue["code"], actor="whs1", now=clock())

        linked_issue = warehouse.create_issue(
            conn,
            lot_code,
            occurred_at=clock(),
            counterpart_name="Tài xế vận tải",
            destination="Công trình tại Thủ Đức",
            transport_trip_code=trip_code,
            lines=[{"lot_line_id": lot_line_id, "quantity": 70}],
            actor="whs1",
            now=clock(),
        )
        warehouse.post_document(conn, linked_issue["code"], actor="whs1", now=clock())

    saved_issue = warehouse.get_document(conn, linked_issue["code"])
    assert saved_issue["transport_trip"]["code"] == trip_code
    assert warehouse.get_lot(conn, lot_code, clock())["lines"][0]["on_hand"] == 0

    current_lot = warehouse.get_lot(conn, lot_code, clock())
    with write_tx(conn):
        closed_lot = warehouse.close_lot(
            conn,
            lot_code,
            expected_version=current_lot["version"],
            no_more_receipts=True,
            actor="whs1",
            now=clock(),
        )
    assert closed_lot["status"] == "COMPLETED"
    assert (
        sales.order_balance(conn, warehouse_order["code"])["order_status"]
        == "COMPLETED"
    )

    vehicle = f.vehicle(conn, clock(), plate="51C-800.01", capacity=2000)
    driver = f.driver(conn, clock(), phone="0908555001")
    with write_tx(conn):
        assignments.assign_trip(
            conn,
            trip_code,
            vehicle_id=vehicle["id"],
            driver_id=driver["id"],
            planned_start=f.vn(8),
            planned_end=f.vn(10),
            actor="dsp1",
            now=clock(),
        )

    clock.set(f.vn(8))
    with write_tx(conn):
        trip_ops.pickup(
            conn,
            trip_code,
            actual_at=f.vn(8),
            handed_over_by="Phạm Văn Nam",
            cargo_condition="70 kiện đã kiểm đếm.",
            actor="dsp1",
            now=clock(),
            actual_weight_kg=900,
        )
    clock.set(f.vn(9))
    with write_tx(conn):
        trip_ops.start_transit(
            conn, trip_code, actual_at=f.vn(9), actor="dsp1", now=clock()
        )
    clock.set(f.vn(10))
    delivery_evidence = f.upload(client, headers=f.DSP)
    with write_tx(conn):
        trip_ops.deliver(
            conn,
            trip_code,
            actual_at=f.vn(10),
            result="SUCCESS",
            receiver_name="Nguyễn Văn Hùng",
            attachment_codes=[delivery_evidence],
            actor="dsp1",
            now=clock(),
        )
        trip_ops.complete(
            conn,
            trip_code,
            released_at=f.vn(10),
            actor="dsp1",
            now=clock(),
        )

    warehouse_balance = sales.order_balance(conn, warehouse_order["code"])
    transport_balance = sales.order_balance(conn, transport_order["code"])
    assert warehouse_balance["order_status"] == "COMPLETED"
    assert transport_balance["order_status"] == "COMPLETED"
    assert warehouse_balance["balance_vnd"] == 500_000
    assert transport_balance["balance_vnd"] == 800_000

    with write_tx(conn):
        warehouse_payment = sales.record_payment(
            conn,
            warehouse_order["code"],
            amount_vnd=500_000,
            occurred_at=clock(),
            method="CASH",
            reference=None,
            note="Thu riêng đơn kho.",
            actor="fin1",
            now=clock(),
        )
        transport_payment = sales.record_payment(
            conn,
            transport_order["code"],
            amount_vnd=800_000,
            occurred_at=clock(),
            method="CASH",
            reference=None,
            note="Thu riêng đơn vận tải.",
            actor="fin1",
            now=clock(),
        )
    assert warehouse_payment["balance"]["balance_vnd"] == 0
    assert transport_payment["balance"]["balance_vnd"] == 0
