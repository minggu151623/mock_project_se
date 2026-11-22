"""Kho trung chuyển (WHS-01→06).

Quy tắc chính:
- BR-10: mỗi dòng hàng có một đơn vị đếm cố định, số lượng nguyên dương; không cộng khác đơn vị.
- BR-11: tồn = tổng nhập − tổng xuất đã POSTED (± điều chỉnh). Nháp không đổi tồn.
  Xác nhận xuất chống tồn âm kể cả khi đồng thời. Không sửa/xóa chứng từ đã xác nhận.
- BR-12: sửa sai bằng phiếu điều chỉnh có nguồn, lý do và MGR duyệt; không vượt lượng nguồn,
  không làm tồn âm.

Chống tồn âm có ba lớp giống engine giữ lịch: kiểm tra trong code, transaction
BEGIN IMMEDIATE của người gọi, và CHECK on_hand >= 0 trong DB.
"""

import sqlite3
from datetime import datetime

from . import attachments, integrations
from .audit import write_audit
from .db import next_code, require_tx
from .errors import Conflict, NotFound, ValidationFailed
from .timeutil import SYSTEM_TZ, from_db, to_db, to_utc
from .validation import NAME_MAX, NOTE_MAX, check_version, clean_text

RECEIPT, ISSUE, ADJUSTMENT = "RECEIPT", "ISSUE", "ADJUSTMENT"
_PREFIX = {RECEIPT: "PN", ISSUE: "PX", ADJUSTMENT: "DC"}
OPEN_LOT = ("WAITING_RECEIPT", "IN_STORAGE")
STORAGE_AGE_NOTE = ("Tuổi lô tính theo ngày lịch từ lần nhập đầu tiên. Không phải tuổi từng kiện "
                    "và không dùng để tự tính phí.")
ISSUE_DRAFT_NOTE = "Phiếu nháp chưa trừ tồn và không giữ trước hàng. Tồn được kiểm tra lại khi xác nhận."


# ---------- Tiện ích ----------

def _upper_code(value: str | None, field: str, max_len: int) -> str:
    return clean_text(value, field, max_len=max_len).upper()


def _qty(value, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValidationFailed("Số lượng phải là số nguyên dương.", details={"field": field})
    return value


def _past_or_now(value: datetime, field: str, now: datetime) -> str:
    moment = to_utc(value)
    if moment > to_utc(now):
        raise ValidationFailed("Thời điểm thực tế không được ở tương lai.", details={"field": field})
    return to_db(moment)


def _optional_time(value: datetime | None) -> str | None:
    return None if value is None else to_db(value)


def _lot_row(conn, code: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM warehouse_lots WHERE code = ?", (code,)).fetchone()
    if row is None:
        raise NotFound("Không tìm thấy lô hàng.")
    return row


def _lot_by_id(conn, lot_id: int) -> sqlite3.Row:
    return conn.execute("SELECT * FROM warehouse_lots WHERE id = ?", (lot_id,)).fetchone()


def _doc_row(conn, code: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM warehouse_documents WHERE code = ?", (code,)).fetchone()
    if row is None:
        raise NotFound("Không tìm thấy chứng từ kho.")
    return row


def _require_open(lot: sqlite3.Row) -> None:
    if lot["status"] not in OPEN_LOT:
        raise Conflict(f"Lô {lot['code']} đang {lot['status']}, không nhận thêm thao tác. "
                       "Nhu cầu mới cần đơn mới.", code="LOT_CLOSED", details={"status": lot["status"]})


def _pending_charges(conn, lot: sqlite3.Row) -> int:
    return integrations.hooks.pending_charge_count(conn, lot["order_code"])


# ---------- Hiển thị ----------

_LEDGER_SQL = """
SELECT dl.lot_line_id AS line_id,
  COALESCE(SUM(CASE WHEN d.doc_type = 'RECEIPT' THEN dl.quantity END), 0) AS received,
  COALESCE(SUM(CASE WHEN d.doc_type = 'ISSUE' THEN dl.quantity END), 0) AS issued,
  COALESCE(SUM(CASE WHEN d.doc_type = 'ADJUSTMENT' AND src.doc_type = 'RECEIPT' THEN dl.quantity END), 0)
      AS receipt_corrections,
  COALESCE(SUM(CASE WHEN d.doc_type = 'ADJUSTMENT' AND src.doc_type = 'ISSUE' THEN dl.quantity END), 0)
      AS issue_corrections
FROM document_lines dl
JOIN warehouse_documents d ON d.id = dl.document_id AND d.status = 'POSTED'
LEFT JOIN warehouse_documents src ON src.id = d.source_document_id
WHERE dl.lot_line_id IN (SELECT id FROM lot_lines WHERE lot_id = ?)
GROUP BY dl.lot_line_id
"""


def ledger_on_hand(conn, lot_id: int) -> dict[int, int]:
    """Tồn tính lại từ chứng từ đã POSTED, dùng để đối chiếu với on_hand lưu sẵn."""
    return {
        r["line_id"]: r["received"] - r["issued"] - r["receipt_corrections"] + r["issue_corrections"]
        for r in conn.execute(_LEDGER_SQL, (lot_id,))
    }


def _storage_age_days(lot: sqlite3.Row, has_stock: bool, now: datetime) -> int | None:
    if not has_stock or lot["first_received_at"] is None or lot["status"] != "IN_STORAGE":
        return None
    first = from_db(lot["first_received_at"]).astimezone(SYSTEM_TZ).date()
    return (to_utc(now).astimezone(SYSTEM_TZ).date() - first).days


def _by_unit(lines: list[dict]) -> dict[str, int]:
    """Tồn theo từng đơn vị. Không bao giờ cộng kiện với pallet (BR-10)."""
    totals: dict[str, int] = {}
    for line in lines:
        totals[line["unit_code"]] = totals.get(line["unit_code"], 0) + line["on_hand"]
    return totals


def _posted_line_ids(conn, lot_id: int) -> set[int]:
    return {r[0] for r in conn.execute(
        "SELECT DISTINCT dl.lot_line_id FROM document_lines dl JOIN warehouse_documents d "
        "ON d.id = dl.document_id WHERE d.lot_id = ? AND d.status = 'POSTED'", (lot_id,))}


def lot_to_dict(conn, lot: sqlite3.Row, now: datetime) -> dict:
    ledger = {r["line_id"]: r for r in conn.execute(_LEDGER_SQL, (lot["id"],))}
    used = _posted_line_ids(conn, lot["id"])
    lines = []
    for r in conn.execute("SELECT * FROM lot_lines WHERE lot_id = ? ORDER BY id", (lot["id"],)):
        led = ledger.get(r["id"])
        lines.append({
            "id": r["id"],
            "item_code": r["item_code"],
            "item_name": r["item_name"],
            "unit_code": r["unit_code"],
            "expected_qty": r["expected_qty"],
            "received": led["received"] if led else 0,
            "issued": led["issued"] if led else 0,
            "receipt_corrections": led["receipt_corrections"] if led else 0,
            "issue_corrections": led["issue_corrections"] if led else 0,
            "on_hand": r["on_hand"],
            "has_transactions": r["id"] in used,
            "version": r["version"],
        })
    return {
        "code": lot["code"],
        "order_code": lot["order_code"],
        "customer_code": lot["customer_code"],
        "status": lot["status"],
        "expected_in_at": lot["expected_in_at"],
        "expected_out_at": lot["expected_out_at"],
        "storage_notes": lot["storage_notes"],
        "first_received_at": lot["first_received_at"],
        "storage_age_days": _storage_age_days(lot, any(x["on_hand"] > 0 for x in lines), now),
        "storage_age_note": STORAGE_AGE_NOTE,
        "on_hand_by_unit": _by_unit(lines),
        "completed_at": lot["completed_at"],
        "completed_by": lot["completed_by"],
        "version": lot["version"],
        "lines": lines,
    }


def get_lot(conn, code: str, now: datetime) -> dict:
    return lot_to_dict(conn, _lot_row(conn, code), now)


def _trip_brief(conn, trip_code: str | None) -> dict | None:
    """Chỉ dữ liệu cần cho giao nhận; không mở hồ sơ khách hàng cho WHS (WHS-04)."""
    if trip_code is None:
        return None
    row = conn.execute(
        "SELECT t.code, t.status, t.pickup_address, t.delivery_address, b.planned_start, b.planned_end, "
        "v.plate_number FROM transport_trips t LEFT JOIN resource_bookings b ON b.id = t.booking_id "
        "LEFT JOIN vehicles v ON v.id = b.vehicle_id WHERE t.code = ?", (trip_code,)).fetchone()
    return None if row is None else dict(row)


def _pending_adjust(conn, source_line_id: int, exclude_doc_id: int | None = None) -> int:
    return conn.execute(
        "SELECT COALESCE(SUM(dl.quantity), 0) FROM document_lines dl JOIN warehouse_documents d "
        "ON d.id = dl.document_id WHERE dl.source_line_id = ? AND d.status = 'DRAFT' AND d.id IS NOT ?",
        (source_line_id, exclude_doc_id)).fetchone()[0]


def doc_to_dict(conn, doc: sqlite3.Row) -> dict:
    lot = _lot_by_id(conn, doc["lot_id"])
    source = None
    if doc["source_document_id"] is not None:
        source = conn.execute("SELECT code, doc_type FROM warehouse_documents WHERE id = ?",
                              (doc["source_document_id"],)).fetchone()
    lines = []
    for r in conn.execute(
        "SELECT dl.*, ll.item_code, ll.item_name, ll.unit_code, ll.on_hand FROM document_lines dl "
        "JOIN lot_lines ll ON ll.id = dl.lot_line_id WHERE dl.document_id = ? ORDER BY dl.id", (doc["id"],)
    ):
        line = {
            "id": r["id"],
            "lot_line_id": r["lot_line_id"],
            "item_code": r["item_code"],
            "item_name": r["item_name"],
            "unit_code": r["unit_code"],
            "quantity": r["quantity"],
            "note": r["note"],
            "source_line_id": r["source_line_id"],
        }
        if doc["status"] == "DRAFT":
            line["current_on_hand"] = r["on_hand"]
        if doc["status"] == "POSTED" and doc["doc_type"] in (RECEIPT, ISSUE):
            line["adjusted_qty"] = r["adjusted_qty"]
            line["remaining_adjustable"] = r["quantity"] - r["adjusted_qty"] - _pending_adjust(conn, r["id"])
        lines.append(line)
    result = {
        "code": doc["code"],
        "doc_type": doc["doc_type"],
        "status": doc["status"],
        "lot_code": lot["code"],
        "occurred_at": doc["occurred_at"],
        "counterpart_name": doc["counterpart_name"],
        "condition_note": doc["condition_note"],
        "discrepancy_reason": doc["discrepancy_reason"],
        "destination": doc["destination"],
        "transport_trip": _trip_brief(conn, doc["transport_trip_code"]),
        "source_document_code": source["code"] if source else None,
        "source_document_type": source["doc_type"] if source else None,
        "reason": doc["reason"],
        "awaiting_approval": doc["doc_type"] == ADJUSTMENT and doc["status"] == "DRAFT",
        "decision_note": doc["decision_note"],
        "decided_by": doc["decided_by"],
        "decided_at": doc["decided_at"],
        "posted_at": doc["posted_at"],
        "posted_by": doc["posted_by"],
        "version": doc["version"],
        "lines": lines,
    }
    if doc["doc_type"] == ADJUSTMENT:
        result["attachments"] = attachments.list_for(conn, "WAREHOUSE_ADJUSTMENT", doc["code"])
    if doc["doc_type"] == ISSUE and doc["status"] == "DRAFT":
        result["stock_note"] = ISSUE_DRAFT_NOTE
    return result


def get_document(conn, code: str) -> dict:
    return doc_to_dict(conn, _doc_row(conn, code))


def list_documents(conn, lot_code: str) -> list[dict]:
    """Nhật ký chứng từ của lô: đủ phiếu gốc và điều chỉnh để tái tính tồn (WHS-05)."""
    lot = _lot_row(conn, lot_code)
    rows = conn.execute("SELECT * FROM warehouse_documents WHERE lot_id = ? ORDER BY id", (lot["id"],))
    return [doc_to_dict(conn, r) for r in rows.fetchall()]


def list_lots(
    conn, *, now: datetime, q: str | None = None, customer_code: str | None = None, status: str | None = None,
    received_from: datetime | None = None, received_to: datetime | None = None, limit: int = 50, offset: int = 0,
) -> dict:
    """Tra cứu lô theo mã, khách, thời gian nhập, trạng thái (WHS-04)."""
    where, params = [], []
    if q and q.strip():
        where.append("(l.code LIKE ? OR l.order_code LIKE ?)")
        params += [f"%{q.strip().upper()}%"] * 2
    if customer_code:
        where.append("l.customer_code = ?")
        params.append(customer_code.strip())
    if status:
        where.append("l.status = ?")
        params.append(status)
    if received_from is not None:
        where.append("l.first_received_at >= ?")
        params.append(to_db(received_from))
    if received_to is not None:
        where.append("l.first_received_at < ?")
        params.append(to_db(received_to))
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    total = conn.execute("SELECT COUNT(*) FROM warehouse_lots l" + clause, params).fetchone()[0]
    rows = conn.execute("SELECT * FROM warehouse_lots l" + clause + " ORDER BY l.id DESC LIMIT ? OFFSET ?",
                        (*params, limit, offset)).fetchall()
    items = []
    for lot in rows:
        units = {r["unit_code"]: r["qty"] for r in conn.execute(
            "SELECT unit_code, SUM(on_hand) AS qty FROM lot_lines WHERE lot_id = ? GROUP BY unit_code",
            (lot["id"],))}
        items.append({
            "code": lot["code"], "order_code": lot["order_code"], "customer_code": lot["customer_code"],
            "status": lot["status"], "first_received_at": lot["first_received_at"],
            "storage_age_days": _storage_age_days(lot, any(v > 0 for v in units.values()), now),
            "on_hand_by_unit": units,
        })
    return {"items": items, "total": total, "storage_age_note": STORAGE_AGE_NOTE}


def approval_queue(conn, *, limit: int = 50, offset: int = 0) -> dict:
    """Hàng đợi phiếu điều chỉnh chờ MGR duyệt."""
    where = " WHERE doc_type = 'ADJUSTMENT' AND status = 'DRAFT'"
    total = conn.execute("SELECT COUNT(*) FROM warehouse_documents" + where).fetchone()[0]
    rows = conn.execute("SELECT * FROM warehouse_documents" + where + " ORDER BY id LIMIT ? OFFSET ?",
                        (limit, offset)).fetchall()
    return {"items": [doc_to_dict(conn, r) for r in rows], "total": total}


# ---------- Lô và dòng hàng (WHS-01) ----------

def _insert_line(conn, lot_id: int, data: dict, actor: str, now: datetime) -> int:
    item_code = _upper_code(data.get("item_code"), "item_code", 50)
    item_name = clean_text(data.get("item_name"), "item_name", max_len=NAME_MAX)
    unit_code = _upper_code(data.get("unit_code"), "unit_code", 20)
    expected = _qty(data.get("expected_qty"), "expected_qty")
    if conn.execute("SELECT 1 FROM lot_lines WHERE lot_id = ? AND item_code = ?", (lot_id, item_code)).fetchone():
        raise Conflict(f"Mã hàng {item_code} đã có trong lô.", code="DUPLICATE_ITEM",
                       details={"field": "item_code"})
    now_s = to_db(now)
    return conn.execute(
        "INSERT INTO lot_lines(lot_id, item_code, item_name, unit_code, expected_qty, created_at, created_by, "
        "updated_at, updated_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (lot_id, item_code, item_name, unit_code, expected, now_s, actor, now_s, actor)).lastrowid


def create_warehouse_lot(
    conn: sqlite3.Connection, *, order_code: str, customer_code: str, actor: str, now: datetime,
    expected_in_at: datetime | None = None, expected_out_at: datetime | None = None,
    storage_notes: str = "", lines: list[dict] | None = None,
) -> dict:
    """Điểm nối cho SAL-06 (BE1). Một đơn kho có đúng một lô; gọi lại trả lô đã có."""
    require_tx(conn)
    order_code = clean_text(order_code, "order_code", max_len=50)
    existing = conn.execute("SELECT * FROM warehouse_lots WHERE order_code = ?", (order_code,)).fetchone()
    if existing is not None:
        return lot_to_dict(conn, existing, now)
    customer_code = clean_text(customer_code, "customer_code", max_len=50)
    code = next_code(conn, "LOT")
    now_s = to_db(now)
    lot_id = conn.execute(
        "INSERT INTO warehouse_lots(code, order_code, customer_code, expected_in_at, expected_out_at, "
        "storage_notes, status, created_at, created_by, updated_at, updated_by) "
        "VALUES (?, ?, ?, ?, ?, ?, 'WAITING_RECEIPT', ?, ?, ?, ?)",
        (code, order_code, customer_code, _optional_time(expected_in_at), _optional_time(expected_out_at),
         clean_text(storage_notes, "storage_notes", max_len=NOTE_MAX, required=False),
         now_s, actor, now_s, actor)).lastrowid
    for line in lines or []:
        _insert_line(conn, lot_id, line, actor, now)
    write_audit(conn, actor=actor, action="LOT_CREATE", object_type="WarehouseLot", object_code=code, at=now,
                after={"order_code": order_code, "customer_code": customer_code})
    return get_lot(conn, code, now)


def _bump_lot(conn, lot_id: int, actor: str, now: datetime) -> None:
    conn.execute("UPDATE warehouse_lots SET version = version + 1, updated_at = ?, updated_by = ? WHERE id = ?",
                 (to_db(now), actor, lot_id))


def add_line(conn, lot_code: str, *, item_code: str, item_name: str, unit_code: str, expected_qty: int,
             actor: str, now: datetime) -> dict:
    """Thêm dòng hàng. Sau khi đã có giao dịch, loại hàng khác thì thêm dòng mới."""
    require_tx(conn)
    lot = _lot_row(conn, lot_code)
    _require_open(lot)
    line_id = _insert_line(conn, lot["id"], {"item_code": item_code, "item_name": item_name,
                                             "unit_code": unit_code, "expected_qty": expected_qty}, actor, now)
    _bump_lot(conn, lot["id"], actor, now)
    write_audit(conn, actor=actor, action="LOT_LINE_ADD", object_type="WarehouseLot", object_code=lot_code,
                at=now, after={"line_id": line_id, "item_code": item_code.strip().upper()})
    return get_lot(conn, lot_code, now)


def _line_of_lot(conn, lot: sqlite3.Row, line_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM lot_lines WHERE id = ? AND lot_id = ?", (line_id, lot["id"])).fetchone()
    if row is None:
        raise NotFound("Không tìm thấy dòng hàng trong lô này.", details={"field": "line_id"})
    return row


def _line_referenced(conn, line_id: int, statuses: tuple[str, ...]) -> bool:
    marks = ",".join("?" * len(statuses))
    return conn.execute(
        f"SELECT 1 FROM document_lines dl JOIN warehouse_documents d ON d.id = dl.document_id "
        f"WHERE dl.lot_line_id = ? AND d.status IN ({marks}) LIMIT 1", (line_id, *statuses)).fetchone() is not None


def update_line(
    conn, lot_code: str, line_id: int, *, expected_version: int, actor: str, now: datetime,
    item_code: str | None = None, item_name: str | None = None, unit_code: str | None = None,
    expected_qty: int | None = None,
) -> dict:
    require_tx(conn)
    lot = _lot_row(conn, lot_code)
    _require_open(lot)
    row = _line_of_lot(conn, lot, line_id)
    check_version(row["version"], expected_version)
    new_code = row["item_code"] if item_code is None else _upper_code(item_code, "item_code", 50)
    new_unit = row["unit_code"] if unit_code is None else _upper_code(unit_code, "unit_code", 20)
    if (new_code, new_unit) != (row["item_code"], row["unit_code"]) and _line_referenced(
            conn, line_id, ("DRAFT", "POSTED")):
        raise Conflict("Dòng hàng đã có chứng từ nên không đổi mã hoặc đơn vị. Thêm dòng mới nếu là loại hàng khác.",
                       code="LOT_LINE_IN_USE", details={"line_id": line_id})
    if new_code != row["item_code"] and conn.execute(
            "SELECT 1 FROM lot_lines WHERE lot_id = ? AND item_code = ? AND id <> ?",
            (lot["id"], new_code, line_id)).fetchone():
        raise Conflict(f"Mã hàng {new_code} đã có trong lô.", code="DUPLICATE_ITEM", details={"field": "item_code"})
    new_name = row["item_name"] if item_name is None else clean_text(item_name, "item_name", max_len=NAME_MAX)
    new_expected = row["expected_qty"] if expected_qty is None else _qty(expected_qty, "expected_qty")
    conn.execute(
        "UPDATE lot_lines SET item_code = ?, item_name = ?, unit_code = ?, expected_qty = ?, version = version + 1, "
        "updated_at = ?, updated_by = ? WHERE id = ?",
        (new_code, new_name, new_unit, new_expected, to_db(now), actor, line_id))
    _bump_lot(conn, lot["id"], actor, now)
    write_audit(conn, actor=actor, action="LOT_LINE_UPDATE", object_type="WarehouseLot", object_code=lot_code,
                at=now, before={k: row[k] for k in ("item_code", "item_name", "unit_code", "expected_qty")},
                after={"item_code": new_code, "item_name": new_name, "unit_code": new_unit,
                       "expected_qty": new_expected})
    return get_lot(conn, lot_code, now)


def remove_line(conn, lot_code: str, line_id: int, *, expected_version: int, actor: str, now: datetime) -> dict:
    """Chỉ xóa dòng chưa từng xuất hiện trên chứng từ nào (BR-16)."""
    require_tx(conn)
    lot = _lot_row(conn, lot_code)
    _require_open(lot)
    row = _line_of_lot(conn, lot, line_id)
    check_version(row["version"], expected_version)
    if _line_referenced(conn, line_id, ("DRAFT", "POSTED", "CANCELLED")):
        raise Conflict("Dòng hàng đã có trên chứng từ nên không xóa được.", code="LOT_LINE_IN_USE",
                       details={"line_id": line_id})
    conn.execute("DELETE FROM lot_lines WHERE id = ?", (line_id,))
    _bump_lot(conn, lot["id"], actor, now)
    write_audit(conn, actor=actor, action="LOT_LINE_REMOVE", object_type="WarehouseLot", object_code=lot_code,
                at=now, before={"item_code": row["item_code"]})
    return get_lot(conn, lot_code, now)


# ---------- Phiếu nhập/xuất (WHS-02, WHS-03, WHS-04) ----------

def _validate_lines(conn, lot: sqlite3.Row, lines: list[dict]) -> list[tuple[sqlite3.Row, int, str | None]]:
    if not lines:
        raise ValidationFailed("Phiếu cần ít nhất một dòng hàng.", details={"field": "lines"})
    seen, result = set(), []
    for i, item in enumerate(lines):
        line_id = item.get("lot_line_id")
        if line_id in seen:
            raise ValidationFailed("Mỗi dòng hàng chỉ xuất hiện một lần trên phiếu.",
                                   details={"field": f"lines.{i}.lot_line_id"})
        seen.add(line_id)
        row = conn.execute("SELECT * FROM lot_lines WHERE id = ?", (line_id,)).fetchone()
        # Backend kiểm tra quan hệ lô–dòng: không xuất hàng của lô khác bằng cách đổi mã dòng.
        if row is None or row["lot_id"] != lot["id"]:
            raise ValidationFailed("Dòng hàng không thuộc lô này.", details={"field": f"lines.{i}.lot_line_id"})
        note = clean_text(item.get("note"), f"lines.{i}.note", max_len=NOTE_MAX, required=False) or None
        result.append((row, _qty(item.get("quantity"), f"lines.{i}.quantity"), note))
    return result


def _validate_trip_link(conn, lot: sqlite3.Row, trip_code: str | None) -> str | None:
    """WHS-04: cùng khách, chưa hủy/hoàn tất, điểm lấy là kho."""
    if trip_code is None or not trip_code.strip():
        return None
    trip = conn.execute("SELECT * FROM transport_trips WHERE code = ?", (trip_code.strip(),)).fetchone()
    if trip is None:
        raise NotFound("Không tìm thấy đơn vận tải.", details={"field": "transport_trip_code"})
    problems = []
    if trip["customer_code"] != lot["customer_code"]:
        problems.append("khác khách hàng")
    if trip["status"] in ("CANCELLED", "COMPLETED"):
        problems.append(f"chuyến đang {trip['status']}")
    if not trip["pickup_from_warehouse"]:
        problems.append("điểm lấy hàng không phải kho")
    if problems:
        raise Conflict("Không liên kết được với chuyến này: " + ", ".join(problems) + ".",
                       code="TRANSPORT_LINK_INVALID", details={"field": "transport_trip_code"})
    return trip["code"]


def transport_candidates(conn, lot_code: str) -> list[dict]:
    lot = _lot_row(conn, lot_code)
    rows = conn.execute(
        "SELECT code FROM transport_trips WHERE customer_code = ? AND pickup_from_warehouse = 1 "
        "AND status NOT IN ('CANCELLED', 'COMPLETED') ORDER BY id", (lot["customer_code"],)).fetchall()
    return [_trip_brief(conn, r["code"]) for r in rows]


def _insert_doc_lines(conn, doc_id: int, lines) -> None:
    for row, qty, note in lines:
        conn.execute("INSERT INTO document_lines(document_id, lot_line_id, quantity, note) VALUES (?, ?, ?, ?)",
                     (doc_id, row["id"], qty, note))


def create_receipt(
    conn, lot_code: str, *, occurred_at: datetime, counterpart_name: str, lines: list[dict], actor: str,
    now: datetime, condition_note: str = "", discrepancy_reason: str = "",
) -> dict:
    """Lập phiếu nhập nháp (WHS-02). Nháp không đổi tồn."""
    require_tx(conn)
    lot = _lot_row(conn, lot_code)
    _require_open(lot)
    occurred = _past_or_now(occurred_at, "occurred_at", now)
    counterpart = clean_text(counterpart_name, "counterpart_name", max_len=NAME_MAX)
    valid = _validate_lines(conn, lot, lines)
    code = next_code(conn, _PREFIX[RECEIPT])
    now_s = to_db(now)
    doc_id = conn.execute(
        "INSERT INTO warehouse_documents(code, lot_id, doc_type, status, occurred_at, counterpart_name, "
        "condition_note, discrepancy_reason, created_at, created_by, updated_at, updated_by) "
        "VALUES (?, ?, 'RECEIPT', 'DRAFT', ?, ?, ?, ?, ?, ?, ?, ?)",
        (code, lot["id"], occurred, counterpart,
         clean_text(condition_note, "condition_note", max_len=NOTE_MAX, required=False),
         clean_text(discrepancy_reason, "discrepancy_reason", max_len=NOTE_MAX, required=False) or None,
         now_s, actor, now_s, actor)).lastrowid
    _insert_doc_lines(conn, doc_id, valid)
    write_audit(conn, actor=actor, action="WAREHOUSE_DRAFT", object_type="WarehouseDocument", object_code=code,
                at=now, after={"doc_type": RECEIPT, "lot_code": lot_code})
    return get_document(conn, code)


def create_issue(
    conn, lot_code: str, *, occurred_at: datetime, counterpart_name: str, destination: str, lines: list[dict],
    actor: str, now: datetime, transport_trip_code: str | None = None,
) -> dict:
    """Lập phiếu xuất nháp (WHS-03). Nháp hiển thị tồn hiện tại, chưa trừ và không giữ trước."""
    require_tx(conn)
    lot = _lot_row(conn, lot_code)
    if lot["status"] != "IN_STORAGE":
        raise Conflict("Lô chưa có hàng nhập xác nhận hoặc đã đóng, chưa xuất được.", code="INVALID_STATE",
                       details={"status": lot["status"]})
    occurred = _past_or_now(occurred_at, "occurred_at", now)
    counterpart = clean_text(counterpart_name, "counterpart_name", max_len=NAME_MAX)
    destination = clean_text(destination, "destination", max_len=500)
    trip_code = _validate_trip_link(conn, lot, transport_trip_code)
    valid = _validate_lines(conn, lot, lines)
    code = next_code(conn, _PREFIX[ISSUE])
    now_s = to_db(now)
    doc_id = conn.execute(
        "INSERT INTO warehouse_documents(code, lot_id, doc_type, status, occurred_at, counterpart_name, "
        "destination, transport_trip_code, created_at, created_by, updated_at, updated_by) "
        "VALUES (?, ?, 'ISSUE', 'DRAFT', ?, ?, ?, ?, ?, ?, ?, ?)",
        (code, lot["id"], occurred, counterpart, destination, trip_code, now_s, actor, now_s, actor)).lastrowid
    _insert_doc_lines(conn, doc_id, valid)
    write_audit(conn, actor=actor, action="WAREHOUSE_DRAFT", object_type="WarehouseDocument", object_code=code,
                at=now, after={"doc_type": ISSUE, "lot_code": lot_code, "transport_trip_code": trip_code})
    return get_document(conn, code)


def _require_draft(doc: sqlite3.Row) -> None:
    if doc["status"] != "DRAFT":
        raise Conflict("Chỉ sửa hoặc hủy được phiếu nháp. Phiếu đã xác nhận phải sửa bằng phiếu điều chỉnh.",
                       code="INVALID_STATE", details={"status": doc["status"]})


def update_draft(
    conn, doc_code: str, *, expected_version: int, occurred_at: datetime, counterpart_name: str,
    lines: list[dict], actor: str, now: datetime, condition_note: str = "", discrepancy_reason: str = "",
    destination: str = "", transport_trip_code: str | None = None,
) -> dict:
    require_tx(conn)
    doc = _doc_row(conn, doc_code)
    check_version(doc["version"], expected_version)
    _require_draft(doc)
    if doc["doc_type"] == ADJUSTMENT:
        raise Conflict("Phiếu điều chỉnh không sửa được; rút lại rồi lập đề nghị mới.", code="INVALID_STATE")
    lot = _lot_by_id(conn, doc["lot_id"])
    _require_open(lot)
    occurred = _past_or_now(occurred_at, "occurred_at", now)
    counterpart = clean_text(counterpart_name, "counterpart_name", max_len=NAME_MAX)
    valid = _validate_lines(conn, lot, lines)
    if doc["doc_type"] == ISSUE:
        destination = clean_text(destination, "destination", max_len=500)
        trip_code = _validate_trip_link(conn, lot, transport_trip_code)
    else:
        destination, trip_code = None, None
    conn.execute("DELETE FROM document_lines WHERE document_id = ?", (doc["id"],))
    _insert_doc_lines(conn, doc["id"], valid)
    conn.execute(
        "UPDATE warehouse_documents SET occurred_at = ?, counterpart_name = ?, condition_note = ?, "
        "discrepancy_reason = ?, destination = ?, transport_trip_code = ?, version = version + 1, "
        "updated_at = ?, updated_by = ? WHERE id = ?",
        (occurred, counterpart, clean_text(condition_note, "condition_note", max_len=NOTE_MAX, required=False),
         clean_text(discrepancy_reason, "discrepancy_reason", max_len=NOTE_MAX, required=False) or None,
         destination, trip_code, to_db(now), actor, doc["id"]))
    return get_document(conn, doc_code)


def cancel_draft(conn, doc_code: str, *, expected_version: int, reason: str, actor: str, now: datetime) -> dict:
    """Hủy phiếu nháp hoặc rút lại đề nghị điều chỉnh. Không ảnh hưởng tồn."""
    require_tx(conn)
    reason = clean_text(reason, "reason", max_len=NOTE_MAX)
    doc = _doc_row(conn, doc_code)
    check_version(doc["version"], expected_version)
    _require_draft(doc)
    conn.execute(
        "UPDATE warehouse_documents SET status = 'CANCELLED', decision_note = ?, decided_by = ?, decided_at = ?, "
        "version = version + 1, updated_at = ?, updated_by = ? WHERE id = ?",
        (reason, actor, to_db(now), to_db(now), actor, doc["id"]))
    write_audit(conn, actor=actor, action="WAREHOUSE_CANCEL", object_type="WarehouseDocument", object_code=doc_code,
                at=now, before={"status": "DRAFT"}, after={"status": "CANCELLED"}, reason=reason)
    return get_document(conn, doc_code)


def _doc_lines(conn, doc_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT dl.*, ll.item_code, ll.unit_code, ll.on_hand, ll.expected_qty FROM document_lines dl "
        "JOIN lot_lines ll ON ll.id = dl.lot_line_id WHERE dl.document_id = ? ORDER BY dl.id", (doc_id,)).fetchall()


def _change_stock(conn, lot_line_id: int, delta: int, *, actor: str, now: datetime) -> None:
    try:
        conn.execute("UPDATE lot_lines SET on_hand = on_hand + ?, updated_at = ?, updated_by = ? WHERE id = ?",
                     (delta, to_db(now), actor, lot_line_id))
    except sqlite3.IntegrityError as exc:
        # Lưới an toàn CHECK on_hand >= 0: code ở trên đã kiểm tra, nhưng không để lọt tồn âm.
        raise Conflict("Không đủ tồn để thực hiện.", code="INSUFFICIENT_STOCK") from exc


def _mark_posted(conn, doc: sqlite3.Row, *, actor: str, now: datetime, decision_note: str | None = None) -> None:
    conn.execute(
        "UPDATE warehouse_documents SET status = 'POSTED', posted_at = ?, posted_by = ?, decision_note = "
        "COALESCE(?, decision_note), decided_by = CASE WHEN doc_type = 'ADJUSTMENT' THEN ? ELSE decided_by END, "
        "decided_at = CASE WHEN doc_type = 'ADJUSTMENT' THEN ? ELSE decided_at END, version = version + 1, "
        "updated_at = ?, updated_by = ? WHERE id = ?",
        (to_db(now), actor, decision_note, actor, to_db(now), to_db(now), actor, doc["id"]))


def post_document(conn, doc_code: str, *, actor: str, now: datetime) -> dict:
    """Xác nhận phiếu nhập/xuất. Xác nhận lại phiếu đã POSTED không đổi tồn lần nữa."""
    require_tx(conn)
    doc = _doc_row(conn, doc_code)
    if doc["doc_type"] == ADJUSTMENT:
        raise Conflict("Phiếu điều chỉnh cần Quản lý duyệt.", code="INVALID_STATE")
    if doc["status"] == "POSTED":
        return get_document(conn, doc_code)
    if doc["status"] == "CANCELLED":
        raise Conflict("Phiếu đã hủy.", code="INVALID_STATE", details={"status": "CANCELLED"})
    lot = _lot_by_id(conn, doc["lot_id"])
    _require_open(lot)
    lines = _doc_lines(conn, doc["id"])

    if doc["doc_type"] == RECEIPT:
        received = {r["line_id"]: r["received"] for r in conn.execute(_LEDGER_SQL, (lot["id"],))}
        over = [l for l in lines if received.get(l["lot_line_id"], 0) + l["quantity"] > l["expected_qty"]]
        if over and not doc["discrepancy_reason"]:
            raise ValidationFailed(
                "Số thực nhận vượt số dự kiến. Ghi lý do chênh lệch trước khi xác nhận.",
                details={"field": "discrepancy_reason",
                         "lines": [{"item_code": l["item_code"], "expected": l["expected_qty"],
                                    "received_after": received.get(l["lot_line_id"], 0) + l["quantity"]}
                                   for l in over]})
        for line in lines:
            _change_stock(conn, line["lot_line_id"], line["quantity"], actor=actor, now=now)
        if lot["status"] == "WAITING_RECEIPT":
            # Nhập đầu tiên làm lô IN_STORAGE và đơn IN_PROGRESS (WHS-02).
            conn.execute("UPDATE warehouse_lots SET status = 'IN_STORAGE', first_received_at = ?, "
                         "version = version + 1, updated_at = ?, updated_by = ? WHERE id = ?",
                         (doc["occurred_at"], to_db(now), actor, lot["id"]))
            integrations.hooks.operation_started(conn, lot["order_code"], "WAREHOUSE", from_db(doc["occurred_at"]))
    else:
        if lot["status"] != "IN_STORAGE":
            raise Conflict("Lô không ở trạng thái lưu kho.", code="INVALID_STATE")
        _validate_trip_link(conn, lot, doc["transport_trip_code"])
        short = [l for l in lines if l["on_hand"] < l["quantity"]]
        if short:
            raise Conflict(
                "Không đủ tồn tại thời điểm xác nhận. Phiếu vẫn là nháp; xem tồn mới bên dưới.",
                code="INSUFFICIENT_STOCK",
                details={"lines": [{"lot_line_id": l["lot_line_id"], "item_code": l["item_code"],
                                    "unit_code": l["unit_code"], "requested": l["quantity"],
                                    "on_hand": l["on_hand"]} for l in short]})
        for line in lines:
            _change_stock(conn, line["lot_line_id"], -line["quantity"], actor=actor, now=now)
    _mark_posted(conn, doc, actor=actor, now=now)
    write_audit(conn, actor=actor, action="WAREHOUSE_POST", object_type="WarehouseDocument", object_code=doc_code,
                at=now, before={"status": "DRAFT"},
                after={"status": "POSTED", "lines": [(l["item_code"], l["quantity"]) for l in lines]})
    return get_document(conn, doc_code)


# ---------- Điều chỉnh (WHS-05) ----------

def request_adjustment(
    conn, source_doc_code: str, *, lines: list[dict], reason: str, actor: str, now: datetime,
    attachment_codes: list[str] | None = None,
) -> dict:
    """WHS đề nghị: giảm lượng nhập ghi thừa, hoặc hoàn lại lượng xuất ghi thừa. Chờ MGR duyệt."""
    require_tx(conn)
    reason = clean_text(reason, "reason", max_len=NOTE_MAX)
    src = _doc_row(conn, source_doc_code)
    if src["status"] != "POSTED" or src["doc_type"] not in (RECEIPT, ISSUE):
        raise Conflict("Chỉ điều chỉnh được phiếu nhập/xuất đã xác nhận.", code="ADJUSTMENT_SOURCE_INVALID")
    lot = _lot_by_id(conn, src["lot_id"])
    if lot["status"] != "IN_STORAGE":
        raise Conflict("Chỉ điều chỉnh khi đơn kho đang thực hiện.", code="LOT_CLOSED",
                       details={"status": lot["status"]})
    if not lines:
        raise ValidationFailed("Đề nghị cần ít nhất một dòng.", details={"field": "lines"})
    seen, valid = set(), []
    for i, item in enumerate(lines):
        source_line_id = item.get("source_line_id")
        if source_line_id in seen:
            raise ValidationFailed("Mỗi dòng nguồn chỉ xuất hiện một lần.",
                                   details={"field": f"lines.{i}.source_line_id"})
        seen.add(source_line_id)
        sl = conn.execute(
            "SELECT dl.*, ll.on_hand, ll.item_code FROM document_lines dl JOIN lot_lines ll ON ll.id = dl.lot_line_id "
            "WHERE dl.id = ? AND dl.document_id = ?", (source_line_id, src["id"])).fetchone()
        if sl is None:
            raise ValidationFailed("Dòng nguồn không thuộc phiếu này.", details={"field": f"lines.{i}.source_line_id"})
        qty = _qty(item.get("quantity"), f"lines.{i}.quantity")
        remaining = sl["quantity"] - sl["adjusted_qty"] - _pending_adjust(conn, sl["id"])
        if qty > remaining:
            raise Conflict(f"Dòng {sl['item_code']} chỉ còn điều chỉnh được {remaining}.",
                           code="ADJUSTMENT_EXCEEDS_SOURCE",
                           details={"source_line_id": sl["id"], "remaining_adjustable": remaining})
        if src["doc_type"] == RECEIPT and qty > sl["on_hand"]:
            # BR-12: không điều chỉnh nhập giảm dưới lượng đã xuất (NEG-13).
            raise Conflict(f"Giảm nhập {qty} sẽ làm tồn {sl['item_code']} âm (đang còn {sl['on_hand']}).",
                           code="INSUFFICIENT_STOCK",
                           details={"source_line_id": sl["id"], "on_hand": sl["on_hand"]})
        valid.append((sl, qty))
    code = next_code(conn, _PREFIX[ADJUSTMENT])
    now_s = to_db(now)
    doc_id = conn.execute(
        "INSERT INTO warehouse_documents(code, lot_id, doc_type, status, source_document_id, reason, "
        "created_at, created_by, updated_at, updated_by) VALUES (?, ?, 'ADJUSTMENT', 'DRAFT', ?, ?, ?, ?, ?, ?)",
        (code, lot["id"], src["id"], reason, now_s, actor, now_s, actor)).lastrowid
    for sl, qty in valid:
        conn.execute("INSERT INTO document_lines(document_id, lot_line_id, quantity, source_line_id) "
                     "VALUES (?, ?, ?, ?)", (doc_id, sl["lot_line_id"], qty, sl["id"]))
    attachments.link(conn, attachment_codes, owner_type="WAREHOUSE_ADJUSTMENT", owner_code=code, actor=actor,
                     now=now, min_count=0)
    write_audit(conn, actor=actor, action="WAREHOUSE_ADJUST_REQUEST", object_type="WarehouseDocument",
                object_code=code, at=now, after={"source": source_doc_code}, reason=reason)
    return get_document(conn, code)


def _adjustment(conn, doc_code: str) -> sqlite3.Row:
    doc = _doc_row(conn, doc_code)
    if doc["doc_type"] != ADJUSTMENT:
        raise Conflict("Chỉ duyệt được phiếu điều chỉnh.", code="INVALID_STATE")
    return doc


def approve_adjustment(conn, doc_code: str, *, actor: str, now: datetime, decision_note: str | None = None) -> dict:
    """MGR chấp nhận. Kiểm tra lại tồn và phần còn điều chỉnh được; duyệt lại không tác động lần hai."""
    require_tx(conn)
    decision_note = clean_text(decision_note, "decision_note", max_len=NOTE_MAX, required=False) or None
    doc = _adjustment(conn, doc_code)
    if doc["status"] == "POSTED":
        return get_document(conn, doc_code)
    if doc["status"] == "CANCELLED":
        raise Conflict("Đề nghị đã bị từ chối hoặc rút lại.", code="INVALID_STATE")
    lot = _lot_by_id(conn, doc["lot_id"])
    if lot["status"] != "IN_STORAGE":
        raise Conflict("Chỉ điều chỉnh khi đơn kho đang thực hiện.", code="LOT_CLOSED")
    src_type = conn.execute("SELECT doc_type FROM warehouse_documents WHERE id = ?",
                            (doc["source_document_id"],)).fetchone()["doc_type"]
    lines = conn.execute(
        "SELECT dl.quantity, dl.lot_line_id, s.id AS source_id, s.quantity AS source_qty, s.adjusted_qty, "
        "ll.on_hand, ll.item_code FROM document_lines dl JOIN document_lines s ON s.id = dl.source_line_id "
        "JOIN lot_lines ll ON ll.id = dl.lot_line_id WHERE dl.document_id = ?", (doc["id"],)).fetchall()
    for l in lines:
        if l["quantity"] > l["source_qty"] - l["adjusted_qty"]:
            raise Conflict(f"Dòng {l['item_code']} không còn đủ lượng để điều chỉnh.",
                           code="ADJUSTMENT_EXCEEDS_SOURCE",
                           details={"remaining_adjustable": l["source_qty"] - l["adjusted_qty"]})
        if src_type == RECEIPT and l["quantity"] > l["on_hand"]:
            raise Conflict(f"Điều chỉnh sẽ làm tồn {l['item_code']} âm (đang còn {l['on_hand']}).",
                           code="INSUFFICIENT_STOCK", details={"on_hand": l["on_hand"]})
    for l in lines:
        # Điều chỉnh nhập làm giảm tồn; điều chỉnh xuất làm tăng tồn.
        delta = -l["quantity"] if src_type == RECEIPT else l["quantity"]
        _change_stock(conn, l["lot_line_id"], delta, actor=actor, now=now)
        conn.execute("UPDATE document_lines SET adjusted_qty = adjusted_qty + ? WHERE id = ?",
                     (l["quantity"], l["source_id"]))
    _mark_posted(conn, doc, actor=actor, now=now, decision_note=decision_note)
    write_audit(conn, actor=actor, action="WAREHOUSE_ADJUST_APPROVE", object_type="WarehouseDocument",
                object_code=doc_code, at=now, before={"status": "DRAFT"}, after={"status": "POSTED"},
                reason=decision_note)
    return get_document(conn, doc_code)


def reject_adjustment(conn, doc_code: str, *, decision_note: str, actor: str, now: datetime) -> dict:
    require_tx(conn)
    decision_note = clean_text(decision_note, "decision_note", max_len=NOTE_MAX)
    doc = _adjustment(conn, doc_code)
    _require_draft(doc)
    conn.execute(
        "UPDATE warehouse_documents SET status = 'CANCELLED', decision_note = ?, decided_by = ?, decided_at = ?, "
        "version = version + 1, updated_at = ?, updated_by = ? WHERE id = ?",
        (decision_note, actor, to_db(now), to_db(now), actor, doc["id"]))
    write_audit(conn, actor=actor, action="WAREHOUSE_ADJUST_REJECT", object_type="WarehouseDocument",
                object_code=doc_code, at=now, before={"status": "DRAFT"}, after={"status": "CANCELLED"},
                reason=decision_note)
    return get_document(conn, doc_code)


# ---------- Kết thúc và hủy (WHS-06, SAL-09) ----------

def close_lot(
    conn, lot_code: str, *, expected_version: int, no_more_receipts: bool, actor: str, now: datetime
) -> dict:
    """WHS chủ động kết thúc. Tồn về 0 không tự đóng lô (WHS-03, WHS-06)."""
    require_tx(conn)
    lot = _lot_row(conn, lot_code)
    check_version(lot["version"], expected_version)
    if lot["status"] != "IN_STORAGE":
        raise Conflict("Chỉ kết thúc được lô đang IN_STORAGE.", code="INVALID_STATE",
                       details={"status": lot["status"]})
    if not no_more_receipts:
        raise ValidationFailed("Cần xác nhận không còn đợt nhận hàng tiếp theo.",
                               details={"field": "no_more_receipts"})
    stock = [dict(r) for r in conn.execute(
        "SELECT id AS lot_line_id, item_code, unit_code, on_hand FROM lot_lines WHERE lot_id = ? AND on_hand > 0",
        (lot["id"],))]
    drafts = [dict(r) for r in conn.execute(
        "SELECT code, doc_type FROM warehouse_documents WHERE lot_id = ? AND status = 'DRAFT'", (lot["id"],))]
    pending = _pending_charges(conn, lot)
    if stock or drafts or pending:
        raise Conflict("Chưa kết thúc được dịch vụ kho. Xử lý các việc còn lại bên dưới.",
                       code="LOT_NOT_CLOSABLE",
                       details={"stock_remaining": stock, "draft_documents": drafts, "pending_charges": pending})
    conn.execute("UPDATE warehouse_lots SET status = 'COMPLETED', completed_at = ?, completed_by = ?, "
                 "version = version + 1, updated_at = ?, updated_by = ? WHERE id = ?",
                 (to_db(now), actor, to_db(now), actor, lot["id"]))
    integrations.hooks.operation_completed(conn, lot["order_code"], "WAREHOUSE", now)
    write_audit(conn, actor=actor, action="LOT_CLOSE", object_type="WarehouseLot", object_code=lot_code, at=now,
                before={"status": "IN_STORAGE"}, after={"status": "COMPLETED"})
    return get_lot(conn, lot_code, now)


def cancel_lot(conn, lot_code: str, *, reason: str, actor: str, now: datetime) -> dict:
    """Điểm nối SAL-09: chỉ hủy khi chưa có phiếu nhập xác nhận (BR-15)."""
    require_tx(conn)
    reason = clean_text(reason, "reason", max_len=NOTE_MAX)
    lot = _lot_row(conn, lot_code)
    if lot["status"] != "WAITING_RECEIPT":
        raise Conflict(f"Lô đang {lot['status']}, đã có nhập xác nhận nên không hủy được theo quy trình chuẩn.",
                       code="NOT_CANCELLABLE", details={"status": lot["status"]})
    conn.execute(
        "UPDATE warehouse_documents SET status = 'CANCELLED', decision_note = ?, decided_by = ?, decided_at = ?, "
        "version = version + 1, updated_at = ?, updated_by = ? WHERE lot_id = ? AND status = 'DRAFT'",
        (reason, actor, to_db(now), to_db(now), actor, lot["id"]))
    conn.execute("UPDATE warehouse_lots SET status = 'CANCELLED', version = version + 1, updated_at = ?, "
                 "updated_by = ? WHERE id = ?", (to_db(now), actor, lot["id"]))
    write_audit(conn, actor=actor, action="LOT_CANCEL", object_type="WarehouseLot", object_code=lot_code, at=now,
                before={"status": lot["status"]}, after={"status": "CANCELLED"}, reason=reason)
    return get_lot(conn, lot_code, now)
