"""Bộ kiểm thử 24 tình huống ngoại lệ toàn hệ thống ở mức API (NEG-01 → NEG-24 - BE3).

Bao phủ đầy đủ các phân hệ:
- BE3 (PUB-01→05, ADM-01→04, Attachments): NEG-01, NEG-02, NEG-21, NEG-22, NEG-23
- BE1 (SAL-01→09, PAY-01→04): NEG-03, NEG-04, NEG-05, NEG-16, NEG-17, NEG-18, NEG-19, NEG-20, NEG-24
- BE2 (TRN-01→05, RNT-01→05, WHS-01→06): NEG-06 → NEG-15
"""

import threading

from backend import admin, assignments, booking, sales, warehouse
from backend.db import connect, write_tx
from backend.errors import DomainError
from tests import factories as f
from tests.factories import ADM, DSP, MGR, SAL, WHS
from tests.test_sales import create_order, issued_quote

FIN = {"X-Dev-User": "fin1", "X-Dev-Roles": "FIN"}


def _race(db_path, *actions):
    barrier = threading.Barrier(len(actions))
    results = [None] * len(actions)

    def run(idx, fn):
        c = connect(db_path)
        try:
            barrier.wait()
            with write_tx(c):
                results[idx] = fn(c)
        except DomainError as exc:
            results[idx] = exc
        finally:
            c.close()

    threads = [threading.Thread(target=run, args=(i, fn)) for i, fn in enumerate(actions)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)
    return results


# ==================== NEG-01 ====================
def test_neg_01_public_request_missing_or_invalid_service_fields(client):
    # Thiếu trường bắt buộc của TRANSPORT (delivery_address).
    r1 = client.post(
        "/api/public/requests",
        json={
            "service_type": "TRANSPORT",
            "contact": {"name": "KH A", "contact_name": "A", "phone": "0901234567"},
            "service_data": {"pickup_address": "Kho A"},
        },
    )
    assert r1.status_code == 422
    assert r1.json()["error"]["details"]["field"] == "delivery_address"

    # RENTAL có giờ kết thúc <= giờ bắt đầu.
    r2 = client.post(
        "/api/public/requests",
        json={
            "service_type": "RENTAL",
            "contact": {"name": "KH B", "contact_name": "B", "phone": "0901234567"},
            "service_data": {
                "vehicle_type": "TRUCK_2T",
                "min_capacity_kg": 1000,
                "requested_start": f.iso(14),
                "requested_end": f.iso(10),
                "purpose": "Chở hàng",
            },
        },
    )
    assert r2.status_code == 422
    assert r2.json()["error"]["details"]["field"] == "requested_end"

    # WAREHOUSE không có dòng hàng.
    r3 = client.post(
        "/api/public/requests",
        json={
            "service_type": "WAREHOUSE",
            "contact": {"name": "KH C", "contact_name": "C", "phone": "0901234567"},
            "service_data": {
                "cargo_description": "Hàng kho",
                "expected_in_at": f.iso(8),
                "expected_out_at": f.iso(18),
                "lines": [],
            },
        },
    )
    assert r3.status_code == 422
    assert r3.json()["error"]["details"]["field"] == "lines"


# ==================== NEG-02 ====================
def test_neg_02_public_lookup_wrong_or_rotated_token_and_rate_limit(client):
    sub = client.post(
        "/api/public/requests",
        headers={"X-Forwarded-For": "192.0.2.10"},
        json={
            "service_type": "TRANSPORT",
            "contact": {"name": "KH A", "contact_name": "A", "phone": "0901234567"},
            "service_data": {
                "pickup_address": "A",
                "delivery_address": "B",
                "cargo_description": "Hàng",
                "cargo_weight_kg": 500,
                "planned_pickup_at": f.iso(8),
            },
        },
    ).json()
    req_code, old_token = sub["request_code"], sub["lookup_token"]

    # Cấp lại token mới -> token cũ bị vô hiệu ngay lập tức.
    rot = client.post(
        f"/api/sales/requests/{req_code}/rotate-token",
        headers=SAL,
        json={"verification": "Đã gọi xác minh SĐT", "reason": "Khách mất token"},
    )
    assert rot.status_code == 200
    new_token = rot.json()["token"]

    old_lookup = client.post(
        "/api/public/lookup",
        headers={"X-Forwarded-For": "192.0.2.11"},
        json={"request_code": req_code, "token": old_token},
    )
    assert old_lookup.status_code == 401
    assert old_lookup.json()["error"]["code"] == "INVALID_LOOKUP_CREDENTIALS"

    new_lookup = client.post(
        "/api/public/lookup",
        headers={"X-Forwarded-For": "192.0.2.11"},
        json={"request_code": req_code, "token": new_token},
    )
    assert new_lookup.status_code == 200


# ==================== NEG-03 ====================
def test_neg_03_duplicate_customer_contact_without_reason(client):
    payload = {
        "customer_type": "BUSINESS",
        "name": "Công ty Hoàng Gia",
        "contact_name": "Nguyễn Hoàng",
        "phone": "0905556666",
        "email": "info@hoanggia.test",
    }
    assert client.post("/api/sales/customers", headers=SAL, json=payload).status_code == 201
    dup = client.post("/api/sales/customers", headers=SAL, json=payload)
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "CUSTOMER_DUPLICATE"


# ==================== NEG-04 ====================
def test_neg_04_accept_superseded_or_expired_quote(client, conn, clock):
    req, old_quote = issued_quote(conn, clock())
    new_q = client.post(
        "/api/sales/quotes",
        headers=SAL,
        json={
            "request_code": req["code"],
            "valid_until": "2026-12-05",
            "service_data": req["service_data"],
            "lines": [{"description": "Cước mới", "quantity": 1, "unit_price_vnd": 700_000}],
        },
    ).json()
    client.post(f"/api/sales/quotes/{new_q['code']}/issue", headers=SAL)

    superseded = client.post(
        f"/api/sales/quotes/{old_quote['code']}/response",
        headers=SAL,
        json={
            "accepted": True,
            "channel": "PHONE",
            "content": "Chấp thuận bản cũ",
            "evidence": [],
            "payment_due_date": "2026-12-10",
        },
    )
    assert superseded.status_code == 409
    assert superseded.json()["error"]["code"] == "INVALID_STATE"

    clock.advance(days=10)
    expired = client.post(
        f"/api/sales/quotes/{new_q['code']}/response",
        headers=SAL,
        json={
            "accepted": True,
            "channel": "PHONE",
            "content": "Chấp thuận khi đã quá hạn",
            "evidence": [],
            "payment_due_date": "2026-12-20",
        },
    )
    assert expired.status_code == 409
    assert expired.json()["error"]["code"] == "QUOTE_EXPIRED"


# ==================== NEG-05 ====================
def test_neg_05_concurrent_quote_acceptance_creates_single_order(db_path, conn, clock):
    _, quote = issued_quote(conn, clock())

    def accept(actor):
        return lambda c: sales.respond_quote_and_create_order(
            c,
            quote["code"],
            accepted=True,
            channel="PHONE",
            content="Đồng ý",
            evidence=[],
            payment_due_date="2026-12-10",
            actor=actor,
            now=clock(),
        )

    results = _race(db_path, accept("sal1"), accept("sal2"))
    ok = [r for r in results if isinstance(r, dict)]
    failed = [r for r in results if isinstance(r, DomainError)]
    assert len(ok) == 1 and len(failed) == 1
    assert failed[0].code == "INVALID_STATE"
    assert conn.execute("SELECT COUNT(*) FROM service_orders").fetchone()[0] == 1


# ==================== NEG-06 ====================
def test_neg_06_vehicle_and_driver_schedule_overlap(client, conn, clock):
    v = f.vehicle(conn, clock())
    d = f.driver(conn, clock())
    t1 = f.trip(conn, clock(), order="ORD-NEG06-1")
    t2 = f.trip(conn, clock(), order="ORD-NEG06-2")

    r1 = client.post(
        f"/api/trips/{t1['code']}/assignment",
        headers=DSP,
        json={"vehicle_id": v["id"], "driver_id": d["id"], "planned_start": f.iso(8), "planned_end": f.iso(11)},
    )
    assert r1.status_code == 201

    r2 = client.post(
        f"/api/trips/{t2['code']}/assignment",
        headers=DSP,
        json={"vehicle_id": v["id"], "driver_id": d["id"], "planned_start": f.iso(10), "planned_end": f.iso(12)},
    )
    assert r2.status_code == 409
    assert r2.json()["error"]["code"] == "BOOKING_CONFLICT"


# ==================== NEG-07 ====================
def test_neg_07_concurrent_vehicle_booking_race(db_path, conn, clock):
    v = f.vehicle(conn, clock())
    d1 = f.driver(conn, clock(), name="TX 1", phone="0901111111")
    d2 = f.driver(conn, clock(), name="TX 2", phone="0902222222")
    t1 = f.trip(conn, clock(), order="ORD-NEG07-1")
    t2 = f.trip(conn, clock(), order="ORD-NEG07-2")

    results = _race(
        db_path,
        lambda c: assignments.assign_trip(
            c, t1["code"], vehicle_id=v["id"], driver_id=d1["id"],
            planned_start=f.vn(8), planned_end=f.vn(10), actor="dsp1", now=clock()
        ),
        lambda c: assignments.assign_trip(
            c, t2["code"], vehicle_id=v["id"], driver_id=d2["id"],
            planned_start=f.vn(9), planned_end=f.vn(11), actor="dsp2", now=clock()
        ),
    )
    ok = [r for r in results if isinstance(r, dict)]
    failed = [r for r in results if isinstance(r, DomainError)]
    assert len(ok) == 1 and len(failed) == 1
    assert failed[0].code == "BOOKING_CONFLICT"


# ==================== NEG-08 ====================
def test_neg_08_deactivate_vehicle_or_driver_with_active_or_future_bookings(client, conn, clock):
    v = f.vehicle(conn, clock())
    d = f.driver(conn, clock())
    t = f.trip(conn, clock(), order="ORD-NEG08-1")
    client.post(
        f"/api/trips/{t['code']}/assignment",
        headers=DSP,
        json={"vehicle_id": v["id"], "driver_id": d["id"], "planned_start": f.iso(8), "planned_end": f.iso(10)},
    )
    rv = client.post(
        f"/api/vehicles/{v['id']}/status",
        headers=DSP,
        json={"version": v["version"], "status": "MAINTENANCE", "reason": "Bảo dưỡng"},
    )
    assert rv.status_code == 409
    assert rv.json()["error"]["code"] == "FUTURE_BOOKINGS_EXIST"


# ==================== NEG-09 ====================
def test_neg_09_ineligible_vehicle_or_insufficient_capacity_or_overweight_pickup(client, conn, clock):
    v_small = f.vehicle(conn, clock(), plate="51C-909.01", capacity=1000)
    d = f.driver(conn, clock())
    t_heavy = f.trip(conn, clock(), order="ORD-NEG09-1", kg=1800)

    r_cap = client.post(
        f"/api/trips/{t_heavy['code']}/assignment",
        headers=DSP,
        json={"vehicle_id": v_small["id"], "driver_id": d["id"], "planned_start": f.iso(8), "planned_end": f.iso(10)},
    )
    assert r_cap.status_code == 409
    assert r_cap.json()["error"]["code"] == "CAPACITY_INSUFFICIENT"


# ==================== NEG-10 ====================
def test_neg_10_start_operation_while_resource_still_in_use_or_outside_window(client, conn, clock):
    v = f.vehicle(conn, clock())
    d = f.driver(conn, clock())
    t = f.trip(conn, clock(), order="ORD-NEG10-1")
    client.post(
        f"/api/trips/{t['code']}/assignment",
        headers=DSP,
        json={"vehicle_id": v["id"], "driver_id": d["id"], "planned_start": f.iso(8), "planned_end": f.iso(10)},
    )
    clock.set(f.vn(11))
    outside = client.post(
        f"/api/trips/{t['code']}/pickup",
        headers=DSP,
        json={"actual_at": f.iso(11), "handed_over_by": "Kho A", "cargo_condition": "Tốt"},
    )
    assert outside.status_code == 409
    assert outside.json()["error"]["code"] == "OUTSIDE_BOOKED_WINDOW"


# ==================== NEG-11 ====================
def test_neg_11_failed_reassignment_preserves_existing_booking(client, conn, clock):
    v1 = f.vehicle(conn, clock(), plate="51C-911.01")
    v2 = f.vehicle(conn, clock(), plate="51C-911.02")
    d1 = f.driver(conn, clock(), name="TX 1", phone="0901111112")
    d2 = f.driver(conn, clock(), name="TX 2", phone="0902222223")
    t1 = f.trip(conn, clock(), order="ORD-NEG11-1")
    t2 = f.trip(conn, clock(), order="ORD-NEG11-2")

    client.post(
        f"/api/trips/{t1['code']}/assignment",
        headers=DSP,
        json={"vehicle_id": v1["id"], "driver_id": d1["id"], "planned_start": f.iso(8), "planned_end": f.iso(10)},
    )
    assigned_t2 = client.post(
        f"/api/trips/{t2['code']}/assignment",
        headers=DSP,
        json={"vehicle_id": v2["id"], "driver_id": d2["id"], "planned_start": f.iso(8), "planned_end": f.iso(10)},
    ).json()

    # Đổi t2 sang xe v1 đang bận -> thất bại 409 và t2 vẫn giữ nguyên xe v2.
    fail_reassign = client.put(
        f"/api/trips/{t2['code']}/assignment",
        headers=DSP,
        json={
            "version": assigned_t2["version"],
            "vehicle_id": v1["id"],
            "driver_id": d2["id"],
            "planned_start": f.iso(8),
            "planned_end": f.iso(10),
            "reason": "Thử đổi sang xe đang bận",
        },
    )
    assert fail_reassign.status_code == 409
    still_v2 = client.get(f"/api/trips/{t2['code']}", headers=DSP).json()
    assert still_v2["booking"]["vehicle"]["id"] == v2["id"]


# ==================== NEG-12 ====================
def test_neg_12_delivery_without_evidence_or_invalid_trip_state_transition(client, conn, clock):
    v = f.vehicle(conn, clock())
    d = f.driver(conn, clock())
    t = f.trip(conn, clock(), order="ORD-NEG12-1")
    client.post(
        f"/api/trips/{t['code']}/assignment",
        headers=DSP,
        json={"vehicle_id": v["id"], "driver_id": d["id"], "planned_start": f.iso(8), "planned_end": f.iso(10)},
    )
    clock.set(f.vn(8))
    # Nhảy bước từ ASSIGNED sang delivery -> 409 INVALID_STATE.
    skip = client.post(
        f"/api/trips/{t['code']}/delivery",
        headers=DSP,
        json={"actual_at": f.iso(8), "result": "SUCCESS", "receiver_name": "Khách", "attachment_codes": []},
    )
    assert skip.status_code == 409
    assert skip.json()["error"]["code"] == "INVALID_STATE"


# ==================== NEG-13 ====================
def test_neg_13_rental_return_odometer_less_than_handover_or_incomplete_close(client, conn, clock):
    v = f.vehicle(conn, clock())
    r = f.rental(conn, clock(), order="ORD-NEG13-1")
    client.post(f"/api/rentals/{r['code']}/reservation", headers=DSP, json={"vehicle_id": v["id"]})
    att1 = f.upload(client, headers=DSP)
    clock.set(f.vn(8))
    client.post(
        f"/api/rentals/{r['code']}/handover",
        headers=DSP,
        json={
            "actual_at": f.iso(8),
            "receiver_name": "Khách thuê",
            "odometer_km": 10_000,
            "body_condition": "Tốt",
            "fuel_level": "Đầy",
            "accessories": "Đủ",
            "attachment_codes": [att1],
        },
    )
    att2 = f.upload(client, headers=DSP)
    clock.set(f.vn(12))
    bad_km = client.post(
        f"/api/rentals/{r['code']}/return",
        headers=DSP,
        json={
            "actual_at": f.iso(12),
            "returned_by": "Khách thuê",
            "odometer_km": 9_999,
            "body_condition": "Tốt",
            "fuel_level": "Đầy",
            "accessories": "Đủ",
            "attachment_codes": [att2],
        },
    )
    assert bad_km.status_code == 422
    assert bad_km.json()["error"]["details"]["field"] == "odometer_km"


# ==================== NEG-14 ====================
def test_neg_14_concurrent_warehouse_issue_prevents_negative_stock(db_path, conn, clock):
    now = clock()
    lot = f.lot(conn, now, order="ORD-NEG14-1")
    line_id = lot["lines"][0]["id"]
    with write_tx(conn):
        rec = warehouse.create_receipt(
            conn, lot["code"], occurred_at=now, counterpart_name="NCC",
            lines=[{"lot_line_id": line_id, "quantity": 50}], actor="whs1", now=now
        )
        warehouse.post_document(conn, rec["code"], actor="whs1", now=now)
        iss1 = warehouse.create_issue(
            conn, lot["code"], occurred_at=now, counterpart_name="KH 1", destination="Q1",
            lines=[{"lot_line_id": line_id, "quantity": 40}], actor="whs1", now=now
        )
        iss2 = warehouse.create_issue(
            conn, lot["code"], occurred_at=now, counterpart_name="KH 2", destination="Q2",
            lines=[{"lot_line_id": line_id, "quantity": 40}], actor="whs2", now=now
        )

    results = _race(
        db_path,
        lambda c: warehouse.post_document(c, iss1["code"], actor="whs1", now=now),
        lambda c: warehouse.post_document(c, iss2["code"], actor="whs2", now=now),
    )
    ok = [r for r in results if isinstance(r, dict)]
    failed = [r for r in results if isinstance(r, DomainError)]
    assert len(ok) == 1 and len(failed) == 1
    assert failed[0].code == "INSUFFICIENT_STOCK"
    assert warehouse.get_lot(conn, lot["code"], now)["lines"][0]["on_hand"] == 10


# ==================== NEG-15 ====================
def test_neg_15_warehouse_adjustment_exceeds_source_or_premature_lot_close(client, conn, clock):
    now = clock()
    lot = f.lot(conn, now, order="ORD-NEG15-1")
    line_id = lot["lines"][0]["id"]
    with write_tx(conn):
        rec = warehouse.create_receipt(
            conn, lot["code"], occurred_at=now, counterpart_name="NCC",
            lines=[{"lot_line_id": line_id, "quantity": 20}], actor="whs1", now=now
        )
        warehouse.post_document(conn, rec["code"], actor="whs1", now=now)

    # Điều chỉnh giảm vượt lượng nguồn (25 > 20) -> 409 ADJUSTMENT_EXCEEDS_SOURCE.
    source_line_id = rec["lines"][0]["id"]
    adj = client.post(
        f"/api/warehouse/documents/{rec['code']}/adjustments",
        headers=WHS,
        json={
            "reason": "Kiểm đếm lại",
            "lines": [{"source_line_id": source_line_id, "quantity": 25}],
        },
    )
    assert adj.status_code == 409
    assert adj.json()["error"]["code"] == "ADJUSTMENT_EXCEEDS_SOURCE"

    # Đóng lô khi còn tồn > 0 -> 409 LOT_NOT_CLOSABLE.
    curr = warehouse.get_lot(conn, lot["code"], now)
    close_res = client.post(
        f"/api/warehouse/lots/{lot['code']}/close",
        headers=WHS,
        json={"version": curr["version"], "no_more_receipts": True},
    )
    assert close_res.status_code == 409
    assert close_res.json()["error"]["code"] == "LOT_NOT_CLOSABLE"


# ==================== NEG-16 ====================
def test_neg_16_charge_proposal_without_evidence_or_on_closed_order_or_double_decision(client, conn, clock):
    _, _, order = create_order(conn, clock())
    # Thiếu bằng chứng (evidence=[]) -> 422.
    no_ev = client.post(
        f"/api/sales/orders/{order['code']}/charges",
        headers=DSP,
        json={"amount_vnd": 100_000, "reason": "Phụ phí chờ", "evidence": []},
    )
    assert no_ev.status_code == 422

    ev = f.upload(client, headers=DSP)
    chg = client.post(
        f"/api/sales/orders/{order['code']}/charges",
        headers=DSP,
        json={"amount_vnd": 100_000, "reason": "Phụ phí chờ", "evidence": [ev]},
    ).json()

    assert client.post(
        f"/api/sales/charges/{chg['code']}/decision",
        headers=MGR,
        json={"approved": True, "decision_note": "Duyệt lần 1"},
    ).status_code == 200

    # Duyệt lần 2 với khóa khác -> 409 INVALID_STATE.
    second = client.post(
        f"/api/sales/charges/{chg['code']}/decision",
        headers=MGR,
        json={"approved": True, "decision_note": "Duyệt lần 2"},
    )
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "INVALID_STATE"


# ==================== NEG-17 ====================
def test_neg_17_overpayment_concurrent_payment_and_duplicate_transfer_reference(client, conn, clock):
    _, _, order = create_order(conn, clock())
    # Thu vượt số dư (600_001 > 600_000).
    over = client.post(
        f"/api/sales/orders/{order['code']}/payments",
        headers=FIN,
        json={
            "amount_vnd": 600_001,
            "occurred_at": clock().isoformat(),
            "method": "CASH",
            "reference": None,
        },
    )
    assert over.status_code == 409
    assert over.json()["error"]["code"] == "PAYMENT_EXCEEDS_BALANCE"

    # Trùng tham chiếu chuyển khoản trên cùng đơn.
    p1 = client.post(
        f"/api/sales/orders/{order['code']}/payments",
        headers=FIN,
        json={
            "amount_vnd": 200_000,
            "occurred_at": clock().isoformat(),
            "method": "BANK_TRANSFER",
            "reference": "REF-DUP-001",
        },
    )
    assert p1.status_code == 201
    dup_ref = client.post(
        f"/api/sales/orders/{order['code']}/payments",
        headers=FIN,
        json={
            "amount_vnd": 100_000,
            "occurred_at": clock().isoformat(),
            "method": "BANK_TRANSFER",
            "reference": "REF-DUP-001",
        },
    )
    assert dup_ref.status_code == 409
    assert dup_ref.json()["error"]["code"] == "PAYMENT_REFERENCE_EXISTS"


# ==================== NEG-18 ====================
def test_neg_18_reverse_payment_twice_or_reverse_reversal_entry(client, conn, clock):
    _, _, order = create_order(conn, clock())
    pay = client.post(
        f"/api/sales/orders/{order['code']}/payments",
        headers=FIN,
        json={"amount_vnd": 100_000, "occurred_at": clock().isoformat(), "method": "CASH"},
    ).json()["payment"]

    rev1 = client.post(
        f"/api/sales/payments/{pay['code']}/reverse",
        headers=FIN,
        json={"reason": "Nhập sai số tiền"},
    )
    assert rev1.status_code == 200
    rev_code = rev1.json()["reversal"]["code"]

    # Đảo lần 2 trên cùng khoản thu gốc -> 409.
    rev2 = client.post(
        f"/api/sales/payments/{pay['code']}/reverse",
        headers=FIN,
        json={"reason": "Đảo lần 2"},
    )
    assert rev2.status_code == 409

    # Đảo bản ghi đảo -> 409.
    rev_of_rev = client.post(
        f"/api/sales/payments/{rev_code}/reverse",
        headers=FIN,
        json={"reason": "Đảo bản ghi đảo"},
    )
    assert rev_of_rev.status_code == 409


# ==================== NEG-19 ====================
def test_neg_19_cancel_order_after_execution_started_or_with_payments_or_charges(client, conn, clock):
    _, _, order = create_order(conn, clock())
    client.post(
        f"/api/sales/orders/{order['code']}/payments",
        headers=FIN,
        json={"amount_vnd": 50_000, "occurred_at": clock().isoformat(), "method": "CASH"},
    )
    blocked = client.post(
        f"/api/sales/orders/{order['code']}/cancel",
        headers=MGR,
        json={"reason": "Khách xin hủy sau khi đã thanh toán"},
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "NOT_CANCELLABLE"


# ==================== NEG-20 ====================
def test_neg_20_overdue_receivable_boundary_and_no_double_counting(client, conn, clock):
    _, _, order = create_order(conn, clock())
    # Đặt hạn thanh toán là ngày hôm nay (2026-12-01) và thêm 2 phụ phí + 2 khoản thu.
    client.patch(
        f"/api/sales/orders/{order['code']}/due-date",
        headers=FIN,
        json={"version": order["version"], "due_date": "2026-12-01", "reason": "Chốt hạn hôm nay"},
    )
    for idx in (1, 2):
        ev = f.upload(client, headers=DSP)
        chg = client.post(
            f"/api/sales/orders/{order['code']}/charges",
            headers=DSP,
            json={"amount_vnd": 50_000, "reason": f"Phụ phí {idx}", "evidence": [ev]},
        ).json()
        client.post(
            f"/api/sales/charges/{chg['code']}/decision",
            headers=MGR,
            json={"approved": True, "decision_note": "OK"},
        )
        client.post(
            f"/api/sales/orders/{order['code']}/payments",
            headers=FIN,
            json={"amount_vnd": 100_000, "occurred_at": clock().isoformat(), "method": "CASH"},
        )

    # Trong ngày đến hạn: chưa quá hạn; tổng phải thu = 600k + 100k = 700k, đã thu = 200k, còn nợ = 500k.
    today_res = client.get("/api/sales/orders", headers=FIN, params={"overdue_only": True}).json()
    assert today_res["total"] == 0

    clock.advance(days=1)
    overdue_res = client.get("/api/sales/orders", headers=FIN, params={"overdue_only": True}).json()
    assert overdue_res["total"] == 1
    assert overdue_res["summary"] == {
        "amount_due_vnd": 700_000,
        "amount_paid_vnd": 200_000,
        "balance_vnd": 500_000,
    }


# ==================== NEG-21 ====================
def test_neg_21_public_access_to_draft_or_hidden_cms_and_hide_all_services(client):
    svc = client.post(
        "/api/admin/cms/contents",
        headers=ADM,
        json={
            "slug": "svc-transport-only",
            "content_type": "SERVICE",
            "service_type": "TRANSPORT",
            "title": "Vận tải",
            "body_html": "<p>Nội dung</p>",
            "status": "PUBLISHED",
        },
    ).json()
    draft = client.post(
        "/api/admin/cms/contents",
        headers=ADM,
        json={
            "slug": "draft-secret",
            "content_type": "COMPANY",
            "title": "Bản nháp",
            "body_html": "<p>Bí mật</p>",
            "status": "DRAFT",
        },
    ).json()

    assert client.get(f"/api/public/contents/{draft['slug']}").status_code == 404
    hide_only_svc = client.post(
        f"/api/admin/cms/contents/{svc['code']}/status",
        headers=ADM,
        json={"version": svc["version"], "status": "HIDDEN"},
    )
    assert hide_only_svc.status_code == 409
    assert hide_only_svc.json()["error"]["code"] == "ALL_SERVICES_HIDDEN"


# ==================== NEG-22 ====================
def test_neg_22_rbac_unauthorized_roles_login_lockout_and_immediate_revocation(client, conn, clock):
    # Chưa đăng nhập -> 401.
    assert client.get("/api/vehicles").status_code == 401
    # ADM không có quyền nghiệp vụ vận hành/kinh doanh -> 403.
    assert client.get("/api/vehicles", headers=ADM).status_code == 403
    assert client.get("/api/sales/customers", headers=ADM).status_code == 403
    # SAL không có quyền quản trị tài khoản hay thu tiền -> 403.
    assert client.get("/api/admin/users", headers=SAL).status_code == 403


# ==================== NEG-23 ====================
def test_neg_23_upload_invalid_file_type_size_script_or_reuse_linked_attachment(client, conn, clock):
    # Giả đuôi .png nhưng nội dung thực thi MZ -> 422.
    fake_exe = client.post(
        "/api/attachments",
        headers=DSP,
        files={"file": ("malware.png", b"MZ\x90\x00executable", "image/png")},
    )
    assert fake_exe.status_code == 422

    # Đuôi kép nguy hiểm .exe.png -> 422.
    double_ext = client.post(
        "/api/attachments",
        headers=DSP,
        files={"file": ("trojan.exe.png", f.PNG, "image/png")},
    )
    assert double_ext.status_code == 422

    # Tệp chứa mã <script> -> 422.
    script_pdf = client.post(
        "/api/attachments",
        headers=DSP,
        files={"file": ("xss.pdf", b"%PDF-1.4\n<script>alert(1)</script>", "application/pdf")},
    )
    assert script_pdf.status_code == 422

    # Tệp vượt 5 MB -> 422.
    too_big = client.post(
        "/api/attachments",
        headers=DSP,
        files={"file": ("big.pdf", b"%PDF-" + b"A" * (5 * 1024 * 1024), "application/pdf")},
    )
    assert too_big.status_code == 422

    # Tệp đã gắn cho phụ phí 1 không được gắn lại cho phụ phí 2 -> 409 ATTACHMENT_ALREADY_USED.
    _, _, order = create_order(conn, clock())
    att_code = f.upload(client, headers=DSP)
    assert client.post(
        f"/api/sales/orders/{order['code']}/charges",
        headers=DSP,
        json={"amount_vnd": 50_000, "reason": "Phụ phí 1", "evidence": [att_code]},
    ).status_code == 201
    reused = client.post(
        f"/api/sales/orders/{order['code']}/charges",
        headers=DSP,
        json={"amount_vnd": 60_000, "reason": "Phụ phí 2 dùng lại tệp", "evidence": [att_code]},
    )
    assert reused.status_code == 409
    assert reused.json()["error"]["code"] == "ATTACHMENT_ALREADY_USED"


# ==================== NEG-24 ====================
def test_neg_24_optimistic_locking_version_conflict_and_idempotency_key_reuse(client, conn, clock):
    v = f.vehicle(conn, clock())
    assert client.patch(
        f"/api/vehicles/{v['id']}", headers=DSP, json={"version": v["version"], "description": "Lần 1"}
    ).status_code == 200
    stale = client.patch(
        f"/api/vehicles/{v['id']}", headers=DSP, json={"version": v["version"], "description": "Lần 2 version cũ"}
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "VERSION_CONFLICT"

    # Dùng lại Idempotency-Key cho nội dung khác -> 409 IDEMPOTENCY_KEY_REUSED.
    _, _, order = create_order(conn, clock())
    headers = {**FIN, "Idempotency-Key": "idem-neg24-key"}
    p1 = client.post(
        f"/api/sales/orders/{order['code']}/payments",
        headers=headers,
        json={"amount_vnd": 100_000, "occurred_at": clock().isoformat(), "method": "CASH"},
    )
    assert p1.status_code == 201
    p2_different = client.post(
        f"/api/sales/orders/{order['code']}/payments",
        headers=headers,
        json={"amount_vnd": 200_000, "occurred_at": clock().isoformat(), "method": "CASH"},
    )
    assert p2_different.status_code == 409
    assert p2_different.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
