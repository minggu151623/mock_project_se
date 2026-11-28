"""WHS-01→06: E2E-03 và NEG-12, 13, 14, 15. Tồn không bao giờ âm, kể cả khi đồng thời."""

import sqlite3
import threading

import pytest

from backend import assignments, integrations, warehouse
from backend.db import connect, write_tx
from backend.errors import Conflict, DomainError
from tests import factories as f
from tests.factories import DSP, MGR, SAL, WHS

W = "/api/warehouse"


@pytest.fixture
def lot(conn, clock):
    return f.lot(conn, clock())


def line_id(lot_dict, index=0):
    return lot_dict["lines"][index]["id"]


def receipt(client, lot_code, line, qty, hour=7, reason=""):
    r = client.post(f"{W}/lots/{lot_code}/receipts", headers=WHS, json={
        "occurred_at": f.iso(hour), "counterpart_name": "Tài xế KH", "discrepancy_reason": reason,
        "lines": [{"lot_line_id": line, "quantity": qty}]})
    assert r.status_code == 201, r.text
    return r.json()


def issue(client, lot_code, line, qty, trip=None):
    r = client.post(f"{W}/lots/{lot_code}/issues", headers=WHS, json={
        "occurred_at": f.iso(7), "counterpart_name": "Tài xế B", "destination": "Cảng Cát Lái",
        "transport_trip_code": trip, "lines": [{"lot_line_id": line, "quantity": qty}]})
    return r


def post_doc(client, code, headers=WHS):
    return client.post(f"{W}/documents/{code}/post", headers=headers)


def on_hand(client, lot_code, index=0):
    return client.get(f"{W}/lots/{lot_code}", headers=WHS).json()["lines"][index]["on_hand"]


def stock_in(client, lot_code, line, qty):
    assert post_doc(client, receipt(client, lot_code, line, qty)["code"]).status_code == 200


def test_post_retry_returns_same_result_without_double_stock(client, conn, lot):
    code, ln = lot["code"], line_id(lot)
    doc = receipt(client, code, ln, 7)
    headers = {**WHS, "Idempotency-Key": "post-document-once"}
    first = client.post(f"{W}/documents/{doc['code']}/post", headers=headers)
    again = client.post(f"{W}/documents/{doc['code']}/post", headers=headers)
    assert first.status_code == again.status_code == 200
    assert first.json() == again.json()
    assert on_hand(client, code) == 7
    assert conn.execute("SELECT count(*) FROM audit_log WHERE action='WAREHOUSE_POST' AND object_code=?",
                        (doc["code"],)).fetchone()[0] == 1


def test_e2e_03_multi_receipt_partial_issue_and_transport_link(client, conn, clock, lot):
    events = []
    integrations.hooks.operation_started = lambda c, o, s, at: events.append(("started", o, s))
    integrations.hooks.operation_completed = lambda c, o, s, at: events.append(("completed", o, s))
    code, ln = lot["code"], line_id(lot)

    stock_in(client, code, ln, 60)
    stock_in(client, code, ln, 40)
    detail = client.get(f"{W}/lots/{code}", headers=WHS).json()
    assert detail["status"] == "IN_STORAGE" and detail["lines"][0]["on_hand"] == 100
    assert events == [("started", lot["order_code"], "WAREHOUSE")]

    draft = issue(client, code, ln, 30).json()
    assert draft["status"] == "DRAFT" and draft["lines"][0]["current_on_hand"] == 100
    assert "chưa trừ tồn" in draft["stock_note"]
    assert on_hand(client, code) == 100
    post_doc(client, draft["code"])
    assert on_hand(client, code) == 70

    trip = f.trip(conn, clock(), order="ORD-T-KHO", customer="KH-001", from_warehouse=True)
    candidates = client.get(f"{W}/lots/{code}/transport-candidates", headers=WHS).json()["items"]
    assert [c["code"] for c in candidates] == [trip["code"]]
    assert "customer_code" not in candidates[0]  # WHS chỉ thấy dữ liệu cần giao nhận
    linked = issue(client, code, ln, 70, trip=trip["code"]).json()
    assert linked["transport_trip"]["code"] == trip["code"]
    post_doc(client, linked["code"])
    assert on_hand(client, code) == 0
    # Liên kết không tự đổi trạng thái chuyến.
    assert client.get(f"/api/trips/{trip['code']}", headers=DSP).json()["status"] == "UNASSIGNED"

    current = client.get(f"{W}/lots/{code}", headers=WHS).json()
    assert current["status"] == "IN_STORAGE"  # tồn về 0 không tự đóng
    closed = client.post(f"{W}/lots/{code}/close", headers=WHS,
                         json={"version": current["version"], "no_more_receipts": True}).json()
    assert closed["status"] == "COMPLETED"
    assert events[-1] == ("completed", lot["order_code"], "WAREHOUSE")
    assert warehouse.ledger_on_hand(conn, conn.execute("SELECT id FROM warehouse_lots WHERE code = ?",
                                                       (code,)).fetchone()[0]) == {ln: 0}
    after = client.post(f"{W}/lots/{code}/receipts", headers=WHS, json={
        "occurred_at": f.iso(7), "counterpart_name": "X", "lines": [{"lot_line_id": ln, "quantity": 1}]})
    assert after.status_code == 409 and after.json()["error"]["code"] == "LOT_CLOSED"


def test_concurrent_issues_never_go_negative_neg_12(client, db_path, conn, clock, lot):
    code, ln = lot["code"], line_id(lot)
    stock_in(client, code, ln, 10)
    drafts = [issue(client, code, ln, 7).json()["code"] for _ in range(2)]
    barrier = threading.Barrier(2)
    results = [None, None]

    def run(i):
        c = connect(db_path)
        try:
            barrier.wait()
            with write_tx(c):
                results[i] = warehouse.post_document(c, drafts[i], actor=f"whs{i}", now=clock())
        except DomainError as exc:
            results[i] = exc
        finally:
            c.close()

    threads = [threading.Thread(target=run, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    failed = [r for r in results if isinstance(r, DomainError)]
    assert len(failed) == 1 and failed[0].code == "INSUFFICIENT_STOCK"
    assert failed[0].details["lines"][0]["on_hand"] == 3
    assert on_hand(client, code) == 3
    statuses = sorted(client.get(f"{W}/documents/{d}", headers=WHS).json()["status"] for d in drafts)
    assert statuses == ["DRAFT", "POSTED"]
    posted = next(d for d in drafts if client.get(f"{W}/documents/{d}", headers=WHS).json()["status"] == "POSTED")
    assert post_doc(client, posted).status_code == 200  # xác nhận lại không trừ lần nữa
    assert on_hand(client, code) == 3


def test_adjustment_limits_neg_13(client, lot):
    code, ln = lot["code"], line_id(lot)
    rec = receipt(client, code, ln, 100)
    post_doc(client, rec["code"])
    out = issue(client, code, ln, 80).json()
    out = post_doc(client, out["code"]).json()
    assert on_hand(client, code) == 20

    rec_line = client.get(f"{W}/documents/{rec['code']}", headers=WHS).json()["lines"][0]["id"]
    blocked = client.post(f"{W}/documents/{rec['code']}/adjustments", headers=WHS, json={
        "reason": "Nhập ghi thừa", "lines": [{"source_line_id": rec_line, "quantity": 30}]})
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "INSUFFICIENT_STOCK"

    out_line = out["lines"][0]["id"]
    adj = client.post(f"{W}/documents/{out['code']}/adjustments", headers=WHS, json={
        "reason": "Xuất ghi thừa 5 kiện", "lines": [{"source_line_id": out_line, "quantity": 5}]}).json()
    assert adj["awaiting_approval"] is True and on_hand(client, code) == 20
    assert client.post(f"{W}/documents/{adj['code']}/approve", headers=WHS, json={}).status_code == 403
    assert client.post(f"{W}/documents/{adj['code']}/post", headers=WHS).json()["error"]["code"] == "INVALID_STATE"

    queue = client.get(f"{W}/approvals", headers=MGR).json()
    assert [d["code"] for d in queue["items"]] == [adj["code"]]
    approved = client.post(f"{W}/documents/{adj['code']}/approve", headers=MGR, json={"decision_note": "Đồng ý"})
    assert approved.json()["status"] == "POSTED" and approved.json()["decided_by"] == "mgr1"
    assert on_hand(client, code) == 25
    client.post(f"{W}/documents/{adj['code']}/approve", headers=MGR, json={})
    assert on_hand(client, code) == 25  # duyệt lại không tác động lần hai

    too_much = client.post(f"{W}/documents/{out['code']}/adjustments", headers=WHS, json={
        "reason": "Thử vượt", "lines": [{"source_line_id": out_line, "quantity": 76}]})
    assert too_much.json()["error"]["code"] == "ADJUSTMENT_EXCEEDS_SOURCE"
    assert too_much.json()["error"]["details"]["remaining_adjustable"] == 75

    second = client.post(f"{W}/documents/{out['code']}/adjustments", headers=WHS, json={
        "reason": "Thêm 2", "lines": [{"source_line_id": out_line, "quantity": 2}]}).json()
    no_note = client.post(f"{W}/documents/{second['code']}/reject", headers=MGR, json={"decision_note": " "})
    assert no_note.status_code == 422
    rejected = client.post(f"{W}/documents/{second['code']}/reject", headers=MGR,
                           json={"decision_note": "Không đủ chứng cứ"}).json()
    assert rejected["status"] == "CANCELLED" and on_hand(client, code) == 25

    journal = client.get(f"{W}/lots/{code}/documents", headers=WHS).json()["items"]
    assert [(d["doc_type"], d["status"]) for d in journal] == [
        ("RECEIPT", "POSTED"), ("ISSUE", "POSTED"), ("ADJUSTMENT", "POSTED"), ("ADJUSTMENT", "CANCELLED")]
    line = client.get(f"{W}/lots/{code}", headers=WHS).json()["lines"][0]
    assert line["received"] - line["issued"] - line["receipt_corrections"] + line["issue_corrections"] == 25


def test_transport_link_rules_neg_14(client, conn, clock, lot):
    code, ln = lot["code"], line_id(lot)
    stock_in(client, code, ln, 10)
    other_customer = f.trip(conn, clock(), order="ORD-T-X1", customer="KH-999", from_warehouse=True)
    not_from_wh = f.trip(conn, clock(), order="ORD-T-X2", customer="KH-001", from_warehouse=False)
    for trip in (other_customer, not_from_wh):
        r = issue(client, code, ln, 5, trip=trip["code"])
        assert r.status_code == 409 and r.json()["error"]["code"] == "TRANSPORT_LINK_INVALID"
    cancelled = f.trip(conn, clock(), order="ORD-T-X3", customer="KH-001", from_warehouse=True)
    with write_tx(conn):
        assignments.cancel_operational_record(conn, source_type="TRANSPORT", source_code=cancelled["code"],
                                              reason="Khách hủy", actor="mgr1", now=clock())
    assert issue(client, code, ln, 5, trip=cancelled["code"]).status_code == 409


def test_units_locked_after_posting_and_never_summed_neg_14(client, lot):
    code, ln = lot["code"], line_id(lot)
    stock_in(client, code, ln, 10)
    version = client.get(f"{W}/lots/{code}", headers=WHS).json()["lines"][0]["version"]
    r = client.patch(f"{W}/lots/{code}/lines/{ln}", headers=WHS, json={"version": version, "unit_code": "PALLET"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "LOT_LINE_IN_USE"
    renamed = client.patch(f"{W}/lots/{code}/lines/{ln}", headers=WHS, json={"version": version, "item_name": "Hàng A1"})
    assert renamed.status_code == 200

    added = client.post(f"{W}/lots/{code}/lines", headers=WHS, json={
        "item_code": "hang-b", "item_name": "Hàng B", "unit_code": "pallet", "expected_qty": 4}).json()
    pallet = next(l for l in added["lines"] if l["item_code"] == "HANG-B")
    stock_in(client, code, pallet["id"], 3)
    assert client.get(f"{W}/lots/{code}", headers=WHS).json()["on_hand_by_unit"] == {"KIEN": 10, "PALLET": 3}


def test_close_blockers_neg_15(client, lot):
    code, ln = lot["code"], line_id(lot)
    stock_in(client, code, ln, 10)
    post_doc(client, issue(client, code, ln, 10).json()["code"])
    draft = issue(client, code, ln, 1).json()
    version = client.get(f"{W}/lots/{code}", headers=WHS).json()["version"]
    r = client.post(f"{W}/lots/{code}/close", headers=WHS, json={"version": version, "no_more_receipts": True})
    assert r.status_code == 409
    assert [d["code"] for d in r.json()["error"]["details"]["draft_documents"]] == [draft["code"]]

    client.post(f"{W}/documents/{draft['code']}/cancel", headers=WHS,
                json={"version": draft["version"], "reason": "Lập nhầm"})
    integrations.hooks.pending_charge_count = lambda c, order: 2
    pending = client.post(f"{W}/lots/{code}/close", headers=WHS, json={"version": version, "no_more_receipts": True})
    assert pending.json()["error"]["details"]["pending_charges"] == 2
    integrations.reset()
    unconfirmed = client.post(f"{W}/lots/{code}/close", headers=WHS, json={"version": version, "no_more_receipts": False})
    assert unconfirmed.status_code == 422
    ok = client.post(f"{W}/lots/{code}/close", headers=WHS, json={"version": version, "no_more_receipts": True})
    assert ok.json()["status"] == "COMPLETED"


def test_lines_of_other_lot_rejected(client, conn, clock, lot):
    other = f.lot(conn, clock(), order="ORD-W-002", customer="KH-002")
    stock_in(client, other["code"], line_id(other), 5)
    stock_in(client, lot["code"], line_id(lot), 5)
    r = issue(client, lot["code"], line_id(other), 1)
    assert r.status_code == 422 and r.json()["error"]["details"]["field"] == "lines.0.lot_line_id"


def test_drafts_editable_posted_documents_final(client, conn, lot):
    code, ln = lot["code"], line_id(lot)
    draft = receipt(client, code, ln, 10)
    assert on_hand(client, code) == 0
    edited = client.put(f"{W}/documents/{draft['code']}", headers=WHS, json={
        "version": draft["version"], "occurred_at": f.iso(7), "counterpart_name": "Tài xế KH",
        "lines": [{"lot_line_id": ln, "quantity": 12}]}).json()
    assert edited["lines"][0]["quantity"] == 12
    post_doc(client, draft["code"])
    assert on_hand(client, code) == 12
    again = client.put(f"{W}/documents/{draft['code']}", headers=WHS, json={
        "version": edited["version"] + 1, "occurred_at": f.iso(7), "counterpart_name": "X",
        "lines": [{"lot_line_id": ln, "quantity": 1}]})
    assert again.status_code == 409
    with pytest.raises(sqlite3.IntegrityError, match="WAREHOUSE_DOCUMENT_FINAL"):
        conn.execute("UPDATE document_lines SET quantity = 1")
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        conn.execute("UPDATE lot_lines SET on_hand = -1")


def test_over_receipt_needs_reason(client, lot):
    code, ln = lot["code"], line_id(lot)
    draft = receipt(client, code, ln, 120)
    r = post_doc(client, draft["code"])
    assert r.status_code == 422 and r.json()["error"]["details"]["field"] == "discrepancy_reason"
    with_reason = receipt(client, code, ln, 120, reason="Khách gửi thêm 20 kiện, đã gọi xác nhận")
    assert post_doc(client, with_reason["code"]).json()["status"] == "POSTED"


def test_cancel_lot_only_before_first_receipt(client, conn, clock, lot):
    with write_tx(conn):
        cancelled = assignments.cancel_operational_record(conn, source_type="WAREHOUSE", source_code=lot["code"],
                                                          reason="Khách hủy", actor="mgr1", now=clock())
    assert cancelled["status"] == "CANCELLED"
    started = f.lot(conn, clock(), order="ORD-W-003")
    stock_in(client, started["code"], line_id(started), 1)
    with pytest.raises(Conflict) as exc, write_tx(conn):
        assignments.cancel_operational_record(conn, source_type="WAREHOUSE", source_code=started["code"],
                                              reason="Khách hủy", actor="mgr1", now=clock())
    assert exc.value.code == "NOT_CANCELLABLE"


def test_roles_and_lookup(client, clock, lot):
    code, ln = lot["code"], line_id(lot)
    draft = receipt(client, code, ln, 5)
    assert post_doc(client, draft["code"], headers=DSP).status_code == 403
    assert client.get(f"{W}/lots/{code}", headers=SAL).status_code == 200
    assert client.post(f"/api/vehicles", headers=WHS, json={
        "plate_number": "51C-1", "vehicle_type": "T", "capacity_kg": 1}).status_code == 403
    post_doc(client, draft["code"])

    clock.set(f.vn(9, day=4))
    listed = client.get(f"{W}/lots", headers=WHS, params={"customer_code": "KH-001"}).json()
    assert listed["items"][0]["storage_age_days"] == 3
    assert "không dùng để tự tính phí" in listed["storage_age_note"]
    assert listed["items"][0]["on_hand_by_unit"] == {"KIEN": 5}
    by_time = client.get(f"{W}/lots", headers=WHS, params={"received_from": f.iso(0, day=2)}).json()
    assert by_time["total"] == 0


def test_lot_creation_is_idempotent_per_order(conn, clock, lot):
    again = f.lot(conn, clock(), order=lot["order_code"])
    assert again["code"] == lot["code"]
