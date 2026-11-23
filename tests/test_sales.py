"""BE1 unit/API coverage for the sales and payment rules."""

import json
from io import BytesIO

import pytest
from pypdf import PdfReader

from backend import sales
from backend import assignments
from backend.db import write_tx
from backend.errors import Conflict, ValidationFailed
from tests import factories as f


def customer(conn, now, phone="0901234567"):
    with write_tx(conn):
        return sales.create_customer(
            conn,
            customer_type="BUSINESS",
            name="Công ty Minh An",
            contact_name="Nguyễn An",
            phone=phone,
            email=f"contact-{phone}@example.com",
            actor="sal1",
            now=now,
        )


def issued_quote(conn, now, *, valid_until="2026-12-02", phone="0901234567"):
    active_customer = customer(conn, now, phone=phone)
    with write_tx(conn):
        request = sales.create_request(
            conn,
            service_type="TRANSPORT",
            source="INTERNAL",
            contact={
                "name": active_customer["name"],
                "contact_name": active_customer["contact_name"],
                "phone": active_customer["phone"],
                "email": active_customer["email"],
            },
            service_data={
                "pickup_address": "25 Lê Lợi, Quận 1",
                "delivery_address": "Khu công nghiệp Tân Bình",
                "cargo_description": "Hàng mẫu",
                "cargo_weight_kg": 500,
                "planned_pickup_at": f.iso(8),
            },
            actor="sal1",
            now=now,
        )
        request = sales.update_request(
            conn,
            request["code"],
            version=request["version"],
            customer_code=active_customer["code"],
            actor="sal1",
            now=now,
        )
        quote = sales.create_quote(
            conn,
            request_code=request["code"],
            valid_until=valid_until,
            service_data=request["service_data"],
            terms="Thanh toán theo thỏa thuận.",
            lines=[
                {
                    "description": "Phí vận chuyển",
                    "quantity": 2,
                    "unit_price_vnd": 250_000,
                },
                {
                    "description": "Phụ phí bốc xếp",
                    "quantity": 1,
                    "unit_price_vnd": 100_000,
                },
            ],
            actor="sal1",
            now=now,
        )
        sales.issue_quote(conn, quote["code"], actor="sal1", now=now)
    return request, quote


def create_order(conn, now, *, phone="0901234567"):
    request, quote = issued_quote(conn, now, phone=phone)
    with write_tx(conn):
        result = sales.respond_quote_and_create_order(
            conn,
            quote["code"],
            accepted=True,
            channel="PHONE",
            content="Khách xác nhận báo giá.",
            evidence=[],
            payment_due_date="2026-12-10",
            actor="sal1",
            now=now,
        )
    return request, quote, result["order"]


def test_duplicate_customer_requires_explicit_reason(conn, clock):
    original = customer(conn, clock())
    with pytest.raises(Conflict) as error:
        customer(conn, clock())
    assert error.value.code == "CUSTOMER_DUPLICATE"
    assert error.value.details["matches"][0]["code"] == original["code"]


def test_service_request_rejects_incomplete_service_data(conn, clock):
    with write_tx(conn), pytest.raises(ValidationFailed) as error:
        sales.create_request(
            conn,
            service_type="TRANSPORT",
            source="INTERNAL",
            contact={"name": "An", "contact_name": "An", "phone": "0901234567"},
            service_data={"pickup_address": "Kho"},
            actor="sal1",
            now=clock(),
        )
    assert error.value.details["field"] == "delivery_address"


@pytest.mark.parametrize(
    ("service_type", "service_data", "field"),
    [
        (
            "RENTAL",
            {
                "vehicle_type": "TRUCK_2T",
                "min_capacity_kg": 1000,
                "requested_start": f.iso(12),
                "requested_end": f.iso(8),
                "purpose": "Chở thiết bị.",
            },
            "requested_end",
        ),
        (
            "WAREHOUSE",
            {
                "cargo_description": "Thiết bị đóng kiện",
                "expected_in_at": f.iso(8),
                "expected_out_at": f.iso(12),
                "lines": [
                    {
                        "item_code": "ITEM-01",
                        "item_name": "Thiết bị",
                        "unit_code": "KIEN",
                        "expected_qty": "invalid",
                    }
                ],
            },
            "lines.0.expected_qty",
        ),
    ],
)
def test_service_request_validates_rental_and_warehouse_fields(
    conn, clock, service_type, service_data, field
):
    with write_tx(conn), pytest.raises(ValidationFailed) as error:
        sales.create_request(
            conn,
            service_type=service_type,
            source="INTERNAL",
            contact={"name": "An", "contact_name": "An", "phone": "0901234567"},
            service_data=service_data,
            actor="sal1",
            now=clock(),
        )
    assert error.value.details["field"] == field


def test_quote_superseded_and_expired_cannot_be_accepted(conn, clock):
    request, old_quote = issued_quote(conn, clock())
    with write_tx(conn):
        newer = sales.create_quote(
            conn,
            request_code=request["code"],
            valid_until="2026-12-03",
            service_data=request["service_data"],
            lines=[
                {
                    "description": "Phí vận chuyển",
                    "quantity": 1,
                    "unit_price_vnd": 10_000,
                }
            ],
            actor="sal1",
            now=clock(),
        )
        sales.issue_quote(conn, newer["code"], actor="sal1", now=clock())
        with pytest.raises(Conflict) as error:
            sales.respond_quote_and_create_order(
                conn,
                old_quote["code"],
                accepted=True,
                channel="PHONE",
                content="Đồng ý",
                evidence=[],
                payment_due_date="2026-12-10",
                actor="sal1",
                now=clock(),
            )
    assert error.value.code == "INVALID_STATE"

    expired_request, expired_quote = issued_quote(
        conn, clock(), valid_until="2026-12-01", phone="0901234568"
    )
    clock.advance(days=1)
    with write_tx(conn), pytest.raises(Conflict) as expired_error:
        sales.respond_quote_and_create_order(
            conn,
            expired_quote["code"],
            accepted=True,
            channel="EMAIL",
            content="Đồng ý",
            evidence=[],
            payment_due_date="2026-12-10",
            actor="sal1",
            now=clock(),
        )
    assert expired_error.value.code == "QUOTE_EXPIRED"


def test_expired_draft_cannot_be_issued(conn, clock):
    request, _ = issued_quote(conn, clock())
    with write_tx(conn):
        draft = sales.create_quote(
            conn,
            request_code=request["code"],
            valid_until="2026-12-02",
            service_data=request["service_data"],
            lines=[
                {
                    "description": "Phí vận chuyển",
                    "quantity": 1,
                    "unit_price_vnd": 5_000,
                }
            ],
            actor="sal1",
            now=clock(),
        )
    clock.advance(days=2)
    with write_tx(conn), pytest.raises(Conflict) as error:
        sales.issue_quote(conn, draft["code"], actor="sal1", now=clock())
    assert error.value.code == "QUOTE_EXPIRED"


def test_rejected_quote_does_not_need_a_payment_due_date(conn, clock):
    _, quote = issued_quote(conn, clock())
    with write_tx(conn):
        response = sales.respond_quote_and_create_order(
            conn,
            quote["code"],
            accepted=False,
            channel="EMAIL",
            content="Khách từ chối do thay đổi kế hoạch.",
            evidence=[],
            payment_due_date=None,
            actor="sal1",
            now=clock(),
        )
    assert response["quote"]["status"] == "REJECTED"
    assert response["order"] is None


def test_expired_draft_cannot_be_issued(conn, clock):
    request, _ = issued_quote(conn, clock())
    with write_tx(conn):
        draft = sales.create_quote(
            conn,
            request_code=request["code"],
            valid_until="2026-12-02",
            service_data=request["service_data"],
            lines=[
                {
                    "description": "Phí vận chuyển",
                    "quantity": 1,
                    "unit_price_vnd": 5_000,
                }
            ],
            actor="sal1",
            now=clock(),
        )
    clock.advance(days=2)
    with write_tx(conn), pytest.raises(Conflict) as error:
        sales.issue_quote(conn, draft["code"], actor="sal1", now=clock())
    assert error.value.code == "QUOTE_EXPIRED"


def test_rejected_quote_does_not_need_a_payment_due_date(conn, clock):
    _, quote = issued_quote(conn, clock())
    with write_tx(conn):
        response = sales.respond_quote_and_create_order(
            conn,
            quote["code"],
            accepted=False,
            channel="EMAIL",
            content="Khách từ chối do thay đổi kế hoạch.",
            evidence=[],
            payment_due_date=None,
            actor="sal1",
            now=clock(),
        )
    assert response["quote"]["status"] == "REJECTED"
    assert response["order"] is None


def test_request_contact_and_token_rotation_are_audited_and_append_only(conn, clock):
    active_customer = customer(conn, clock())
    with write_tx(conn):
        request = sales.create_request(
            conn,
            service_type="TRANSPORT",
            source="INTERNAL",
            contact={"name": "An", "contact_name": "An", "phone": "0901234567"},
            service_data={
                "pickup_address": "Kho",
                "delivery_address": "Cửa hàng",
                "cargo_description": "Thùng hàng",
                "cargo_weight_kg": 100,
                "planned_pickup_at": f.iso(8),
            },
            actor="sal1",
            now=clock(),
            token="old-secret-token",
        )
        sales.update_request(
            conn,
            request["code"],
            version=request["version"],
            customer_code=active_customer["code"],
            clarification="Xác nhận giờ lấy hàng.",
            actor="sal1",
            now=clock(),
        )
        event = sales.record_contact(
            conn,
            request["code"],
            channel="PHONE",
            content="Khách xác nhận lịch.",
            follow_up="Gửi báo giá",
            actual_at=clock(),
            actor="sal1",
            now=clock(),
        )
        contact_event_id = conn.execute(
            "SELECT id FROM request_events WHERE request_code=? AND event_type='CONTACT:PHONE'",
            (request["code"],),
        ).fetchone()[0]
        correction = sales.correct_request_event(
            conn,
            request["code"],
            contact_event_id,
            corrected_content="Khách xác nhận lịch lúc 9 giờ.",
            reason="Ghi nhầm giờ trao đổi.",
            actor="sal1",
            now=clock(),
        )
        rotated = sales.rotate_request_token(
            conn,
            request["code"],
            verification="Đã đối chiếu số điện thoại khách hàng.",
            reason="Khách làm mất token.",
            actor="sal1",
            now=clock(),
        )
    saved = sales.get_request(conn, request["code"])
    assert saved["public_token_hash"] != rotated["token"]
    assert len(rotated["token"]) >= 32
    assert correction["corrects_event_id"] == contact_event_id
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM request_events WHERE request_code=?",
            (request["code"],),
        ).fetchone()[0]
        == 4
    )
    original = conn.execute(
        "SELECT content FROM request_events WHERE id=?", (contact_event_id,)
    ).fetchone()[0]
    assert "Khách xác nhận lịch" in original
    assert event["code"] == request["code"]


def test_order_payment_reversal_charge_and_receivable_consistency(client, conn, clock):
    _, quote, order = create_order(conn, clock())
    assert quote["total_vnd"] == 600_000
    charge_evidence = f.upload(client, headers=f.DSP)

    with write_tx(conn):
        charge = sales.propose_charge(
            conn,
            order["code"],
            amount_vnd=50_000,
            reason="Bốc xếp ngoài giờ",
            evidence=[charge_evidence],
            actor="dsp1",
            now=clock(),
        )
        assert sales.order_balance(conn, order["code"])["amount_due_vnd"] == 600_000
        with pytest.raises(Conflict) as pending_charge:
            sales._operation_status(conn, order["code"], "COMPLETED", clock())
        assert pending_charge.value.code == "PENDING_CHARGES"
        approved = sales.decide_charge(
            conn,
            charge["code"],
            approved=True,
            decision_note="Có bằng chứng kèm theo.",
            actor="mgr1",
            now=clock(),
        )
    assert approved["status"] == "APPROVED"
    assert sales.order_balance(conn, order["code"])["amount_due_vnd"] == 650_000

    with write_tx(conn), pytest.raises(Conflict) as overpay:
        sales.record_payment(
            conn,
            order["code"],
            amount_vnd=650_001,
            occurred_at=clock(),
            method="CASH",
            reference=None,
            note="",
            actor="fin1",
            now=clock(),
        )
    assert overpay.value.code == "PAYMENT_EXCEEDS_BALANCE"

    with write_tx(conn):
        payment = sales.record_payment(
            conn,
            order["code"],
            amount_vnd=650_000,
            occurred_at=clock(),
            method="BANK_TRANSFER",
            reference="UNC-20261201-01",
            note="Thanh toán đủ",
            actor="fin1",
            now=clock(),
        )["payment"]
        with pytest.raises(Conflict) as duplicate_reference:
            sales.record_payment(
                conn,
                order["code"],
                amount_vnd=1,
                occurred_at=clock(),
                method="BANK_TRANSFER",
                reference="UNC-20261201-01",
                note="Tham chiếu đã dùng.",
                actor="fin2",
                now=clock(),
            )
        assert duplicate_reference.value.code == "PAYMENT_REFERENCE_EXISTS"
        sales._operation_status(conn, order["code"], "IN_PROGRESS", clock())
        sales._operation_status(conn, order["code"], "COMPLETED", clock())
        reversal = sales.reverse_payment(
            conn,
            payment["code"],
            reason="Ghi nhận nhầm giao dịch.",
            actor="fin1",
            now=clock(),
        )
    assert reversal["balance"]["balance_vnd"] == 650_000
    assert reversal["balance"]["order_status"] == "COMPLETED"
    with write_tx(conn), pytest.raises(Conflict) as duplicate_reverse:
        sales.reverse_payment(
            conn,
            payment["code"],
            reason="Đảo lần hai.",
            actor="fin1",
            now=clock(),
        )
    assert duplicate_reverse.value.code == "INVALID_STATE"


def test_order_note_uses_optimistic_version_and_preserves_snapshots(conn, clock):
    _, _, order = create_order(conn, clock())
    before = sales.order_detail(conn, order["code"], now=clock())

    with write_tx(conn):
        saved = sales.update_order_contact_note(
            conn,
            order["code"],
            contact_note="Liên hệ chị An trước khi giao.",
            version=before["version"],
            actor="sal1",
            now=clock(),
        )
    assert saved["contact_note"] == "Liên hệ chị An trước khi giao."
    assert json.loads(saved["customer_snapshot_json"]) == before["customer_snapshot"]

    with write_tx(conn), pytest.raises(Conflict) as conflict:
        sales.update_order_contact_note(
            conn,
            order["code"],
            contact_note="Ghi đè từ bản cũ.",
            version=before["version"],
            actor="sal2",
            now=clock(),
        )
    assert conflict.value.code == "VERSION_CONFLICT"


def test_overdue_filter_counts_and_aggregates_orders_once(conn, clock):
    _, _, order = create_order(conn, clock())
    listing = sales.list_orders(conn, overdue_only=True, now=clock())
    assert listing["total"] == 0

    with write_tx(conn):
        sales.update_due_date(
            conn,
            order["code"],
            due_date="2026-12-01",
            reason="Kiểm thử mốc đến hạn.",
            version=order["version"],
            actor="sal1",
            now=clock(),
        )
    on_due_date = sales.list_orders(conn, overdue_only=True, now=clock())
    assert on_due_date["total"] == 0

    clock.advance(days=1)
    overdue = sales.list_orders(
        conn, overdue_only=True, payment_status="UNPAID", now=clock()
    )
    assert overdue["total"] == 1
    assert overdue["items"][0]["code"] == order["code"]
    assert overdue["items"][0]["balance"]["balance_vnd"] == 600_000


def test_due_date_filters_and_summary_cover_all_filtered_pages(conn, clock):
    _, _, first_order = create_order(conn, clock())
    _, _, second_order = create_order(conn, clock(), phone="0901234569")
    with write_tx(conn):
        sales.update_due_date(
            conn,
            second_order["code"],
            due_date="2026-12-12",
            reason="Đổi hạn theo thỏa thuận.",
            version=second_order["version"],
            actor="sal1",
            now=clock(),
        )

    first_due = sales.list_orders(
        conn,
        due_from="2026-12-10",
        due_to="2026-12-10",
        limit=1,
        now=clock(),
    )
    assert first_due["total"] == 1
    assert first_due["items"][0]["code"] == first_order["code"]

    both_due = sales.list_orders(
        conn,
        due_from="2026-12-10",
        due_to="2026-12-12",
        limit=1,
        now=clock(),
    )
    assert both_due["total"] == 2
    assert len(both_due["items"]) == 1
    assert both_due["summary"] == {
        "amount_due_vnd": 1_200_000,
        "amount_paid_vnd": 0,
        "balance_vnd": 1_200_000,
    }


def test_charge_proposal_retry_does_not_create_duplicate(client, conn, clock):
    _, _, order = create_order(conn, clock())
    evidence = f.upload(client, headers=f.DSP)
    payload = {
        "amount_vnd": 40_000,
        "reason": "Bốc xếp ngoài giờ",
        "evidence": [evidence],
    }
    headers = {**f.SAL, "Idempotency-Key": "charge-retry-001"}
    first = client.post(
        f"/api/sales/orders/{order['code']}/charges", headers=headers, json=payload
    )
    second = client.post(
        f"/api/sales/orders/{order['code']}/charges", headers=headers, json=payload
    )
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert first.json()["code"] == second.json()["code"]
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM charges WHERE order_code=?", (order["code"],)
        ).fetchone()[0]
        == 1
    )


def test_manager_cancellation_releases_be2_booking_and_preserves_request(
    client, conn, clock
):
    request, _, order = create_order(conn, clock())
    vehicle = f.vehicle(conn, clock())
    driver = f.driver(conn, clock())
    with write_tx(conn):
        trip = assignments.get_trip(conn, order["operational_code"], clock())
        assignments.assign_trip(
            conn,
            trip["code"],
            vehicle_id=vehicle["id"],
            driver_id=driver["id"],
            planned_start=f.vn(8),
            planned_end=f.vn(10),
            actor="dsp1",
            now=clock(),
        )
    trip = assignments.get_trip(conn, order["operational_code"], clock())

    response = client.post(
        f"/api/sales/orders/{order['code']}/cancel",
        headers={**f.MGR, "Idempotency-Key": "cancel-order-001"},
        json={"reason": "Khách xác nhận dừng dịch vụ."},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "CANCELLED"
    assert sales.get_request(conn, request["code"])["status"] == "CONFIRMED"
    assert assignments.get_trip(conn, trip["code"], clock())["status"] == "CANCELLED"
    assert (
        conn.execute(
            "SELECT status FROM resource_bookings WHERE id=?", (trip["booking"]["id"],)
        ).fetchone()[0]
        == "RELEASED"
    )


def test_order_with_effective_payment_cannot_be_cancelled(conn, clock):
    _, _, order = create_order(conn, clock())
    with write_tx(conn):
        sales.record_payment(
            conn,
            order["code"],
            amount_vnd=10_000,
            occurred_at=clock(),
            method="CASH",
            reference=None,
            note="",
            actor="fin1",
            now=clock(),
        )
        with pytest.raises(Conflict) as error:
            sales.cancel_order(
                conn,
                order["code"],
                reason="Khách muốn hủy.",
                actor="mgr1",
                now=clock(),
            )
    assert error.value.code == "NOT_CANCELLABLE"


def test_schema_migration_records_versions_and_adds_order_snapshot_fields(conn):
    versions = [
        row[0]
        for row in conn.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        )
    ]
    columns = {row[1] for row in conn.execute("PRAGMA table_info(service_orders)")}
    assert versions == [1, 2]
    assert {"customer_snapshot_json", "contact_note"} <= columns


def test_quote_pdf_is_printable(client, conn, clock):
    _, quote = issued_quote(conn, clock())
    response = client.get(f"/api/sales/quotes/{quote['code']}/pdf", headers=f.SAL)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF-1.4")
    assert response.content.endswith(b"%%EOF\n")
    pdf_text = "\n".join(
        page.extract_text() or "" for page in PdfReader(BytesIO(response.content)).pages
    )
    assert "600,000 VND" in pdf_text
    assert "BÁO GIÁ DỊCH VỤ" in pdf_text


def test_replaying_quote_acceptance_returns_same_order(client, conn, clock):
    _, quote = issued_quote(conn, clock())
    payload = {
        "accepted": True,
        "channel": "PHONE",
        "content": "Khách xác nhận qua điện thoại.",
        "evidence": [],
        "payment_due_date": "2026-12-10",
    }
    headers = {**f.SAL, "Idempotency-Key": "acceptance-key-001"}
    first = client.post(
        f"/api/sales/quotes/{quote['code']}/response", headers=headers, json=payload
    )
    second = client.post(
        f"/api/sales/quotes/{quote['code']}/response", headers=headers, json=payload
    )
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["order"]["code"] == second.json()["order"]["code"]
    assert conn.execute("SELECT COUNT(*) FROM service_orders").fetchone()[0] == 1
