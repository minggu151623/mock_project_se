"""Thực hiện thuê xe: bàn giao, nhận lại, hoàn tất (RNT-03/04/05).

Luồng: RESERVED → HANDED_OVER → RETURNED → COMPLETED.
Biên bản đã xác nhận không sửa/xóa; ghi chú bổ sung có tác giả và thời gian.
Trả muộn chỉ hiển thị số phút vượt; không tự tính tiền phạt (RNT-04).
"""

import sqlite3
from datetime import datetime

from . import attachments, booking, integrations
from .assignments import rental_row, rental_to_dict
from .audit import write_audit
from .booking import RENTAL
from .db import require_tx
from .errors import Conflict, ValidationFailed
from .timeutil import from_db, to_db, to_utc
from .validation import NAME_MAX, NOTE_MAX, clean_text

HANDOVER, RETURN = "HANDOVER", "RETURN"
_EVIDENCE = {HANDOVER: "RENTAL_HANDOVER", RETURN: "RENTAL_RETURN"}


def _inspection(conn, code: str, kind: str) -> dict | None:
    row = conn.execute("SELECT * FROM rental_inspections WHERE rental_code = ? AND kind = ?", (code, kind)).fetchone()
    if row is None:
        return None
    data = {k: row[k] for k in ("kind", "actual_at", "person_name", "odometer_km", "body_condition", "fuel_level",
                                "accessories", "note", "recorded_at", "recorded_by")}
    data["attachments"] = attachments.list_for(conn, _EVIDENCE[kind], code)
    return data


def _minutes_late(planned_end: str, actual_or_now: datetime) -> int:
    return max(0, int((to_utc(actual_or_now) - from_db(planned_end)).total_seconds() // 60))


def get_rental_detail(conn, code: str, now: datetime) -> dict:
    detail = rental_to_dict(conn, rental_row(conn, code), now)
    handover, returned = _inspection(conn, code, HANDOVER), _inspection(conn, code, RETURN)
    b = detail["booking"]
    late = 0
    if b is not None:
        if returned is not None:
            late = _minutes_late(b["planned_end"], from_db(returned["actual_at"]))
        elif b["status"] == "IN_USE":
            late = _minutes_late(b["planned_end"], now)
    detail.update({
        "handover": handover,
        "return": returned,
        # Thời gian vượt so với kết thúc booking. Chỉ hiển thị; phí phát sinh đi theo SAL-08.
        "late_minutes": late,
        "overdue": bool(b and b["overdue"]),
        "notes": [dict(r) for r in conn.execute(
            "SELECT note, author, created_at FROM record_notes WHERE owner_type = 'RENTAL' AND owner_code = ? "
            "ORDER BY id", (code,))],
    })
    return detail


def list_rentals(
    conn, *, now: datetime, status: str | None = None, q: str | None = None, overdue_only: bool = False,
    limit: int = 50, offset: int = 0,
) -> dict:
    where, params = [], []
    if status:
        where.append("r.status = ?")
        params.append(status)
    if q and q.strip():
        where.append("(r.code LIKE ? OR r.order_code LIKE ?)")
        params += [f"%{q.strip().upper()}%"] * 2
    if overdue_only:
        where.append("b.status = 'IN_USE' AND b.planned_end < ?")
        params.append(to_db(now))
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    base = " FROM rental_records r LEFT JOIN resource_bookings b ON b.id = r.booking_id" + clause
    total = conn.execute("SELECT COUNT(*)" + base, params).fetchone()[0]
    rows = conn.execute("SELECT r.*" + base + " ORDER BY r.id DESC LIMIT ? OFFSET ?",
                        (*params, limit, offset)).fetchall()
    return {"items": [rental_to_dict(conn, r, now) for r in rows], "total": total}


def _insert_inspection(conn, code: str, kind: str, *, actual_s: str, person: str, odometer_km: int,
                       body_condition: str, fuel_level: str, accessories: str, note: str | None,
                       actor: str, now: datetime) -> None:
    try:
        conn.execute(
            "INSERT INTO rental_inspections(rental_code, kind, actual_at, person_name, odometer_km, body_condition, "
            "fuel_level, accessories, note, recorded_at, recorded_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (code, kind, actual_s, person, odometer_km, body_condition, fuel_level, accessories, note,
             to_db(now), actor))
    except sqlite3.IntegrityError as exc:
        raise Conflict("Biên bản này đã được ghi nhận.", code="INVALID_STATE") from exc


def _clean_inspection(person: str, person_field: str, odometer_km: int, body_condition: str, fuel_level: str,
                      accessories: str, note: str | None) -> tuple:
    if isinstance(odometer_km, bool) or not isinstance(odometer_km, int) or odometer_km < 0:
        raise ValidationFailed("Số kilomet phải là số nguyên không âm.", details={"field": "odometer_km"})
    return (
        clean_text(person, person_field, max_len=NAME_MAX),
        clean_text(body_condition, "body_condition", max_len=NOTE_MAX),
        clean_text(fuel_level, "fuel_level", max_len=200),
        clean_text(accessories, "accessories", max_len=NOTE_MAX),
        clean_text(note, "note", max_len=NOTE_MAX, required=False) or None,
    )


def _set_status(conn, code: str, status: str, *, actor: str, now: datetime) -> None:
    conn.execute("UPDATE rental_records SET status = ?, version = version + 1, updated_at = ?, updated_by = ? "
                 "WHERE code = ?", (status, to_db(now), actor, code))


def _require_status(row: sqlite3.Row, expected: str, action: str) -> None:
    if row["status"] != expected:
        raise Conflict(f"Chỉ hồ sơ {expected} mới {action}. Hồ sơ đang {row['status']}.",
                       code="INVALID_STATE", details={"status": row["status"]})


def handover(
    conn, code: str, *, actual_at: datetime, receiver_name: str, odometer_km: int, body_condition: str,
    fuel_level: str, accessories: str, attachment_codes: list[str] | None, actor: str, now: datetime,
    note: str | None = None,
) -> dict:
    """RNT-03: bàn giao xe. Xe phải thực sự rảnh (BR-09) và giờ giao nằm trong khung đã giữ."""
    require_tx(conn)
    row = rental_row(conn, code)
    _require_status(row, "RESERVED", "được bàn giao xe")
    person, body, fuel, acc, note = _clean_inspection(receiver_name, "receiver_name", odometer_km, body_condition,
                                                      fuel_level, accessories, note)
    started = booking.start_usage(conn, row["booking_id"], actual_start=actual_at, actor=actor, now=now)
    _insert_inspection(conn, code, HANDOVER, actual_s=started["actual_start"], person=person,
                       odometer_km=odometer_km, body_condition=body, fuel_level=fuel, accessories=acc, note=note,
                       actor=actor, now=now)
    attachments.link(conn, attachment_codes, owner_type=_EVIDENCE[HANDOVER], owner_code=code, actor=actor, now=now)
    _set_status(conn, code, "HANDED_OVER", actor=actor, now=now)
    integrations.hooks.operation_started(conn, row["order_code"], RENTAL, from_db(started["actual_start"]))
    write_audit(conn, actor=actor, action="RENTAL_HANDOVER", object_type="RentalRecord", object_code=code, at=now,
                before={"status": "RESERVED"},
                after={"status": "HANDED_OVER", "actual_at": started["actual_start"], "odometer_km": odometer_km})
    return get_rental_detail(conn, code, now)


def return_vehicle(
    conn, code: str, *, actual_at: datetime, returned_by: str, odometer_km: int, body_condition: str,
    fuel_level: str, accessories: str, attachment_codes: list[str] | None, actor: str, now: datetime,
    note: str | None = None,
) -> dict:
    """RNT-04: nhận lại xe và giải phóng xe tại thời điểm thực tế.

    Xe cần sửa thì điều phối đặt MAINTENANCE sau đó qua TRN-01 (phải xử lý lịch tương lai trước).
    """
    require_tx(conn)
    row = rental_row(conn, code)
    _require_status(row, "HANDED_OVER", "được ghi nhận trả xe")
    person, body, fuel, acc, note = _clean_inspection(returned_by, "returned_by", odometer_km, body_condition,
                                                      fuel_level, accessories, note)
    handed = _inspection(conn, code, HANDOVER)
    if odometer_km < handed["odometer_km"]:
        raise ValidationFailed(f"Số kilomet trả ({odometer_km}) không được nhỏ hơn lúc giao ({handed['odometer_km']}).",
                               details={"field": "odometer_km"})
    moment = to_utc(actual_at)
    if moment < from_db(handed["actual_at"]):
        raise ValidationFailed("Thời điểm trả không được trước lúc bàn giao.", details={"field": "actual_at"})
    released = booking.release(conn, row["booking_id"], reason="Nhận lại xe thuê", actual_end=moment,
                               actor=actor, now=now)
    _insert_inspection(conn, code, RETURN, actual_s=released["actual_end"], person=person, odometer_km=odometer_km,
                       body_condition=body, fuel_level=fuel, accessories=acc, note=note, actor=actor, now=now)
    attachments.link(conn, attachment_codes, owner_type=_EVIDENCE[RETURN], owner_code=code, actor=actor, now=now)
    _set_status(conn, code, "RETURNED", actor=actor, now=now)
    write_audit(conn, actor=actor, action="RENTAL_RETURN", object_type="RentalRecord", object_code=code, at=now,
                before={"status": "HANDED_OVER"},
                after={"status": "RETURNED", "actual_at": released["actual_end"], "odometer_km": odometer_km})
    return get_rental_detail(conn, code, now)


def complete_rental(conn, code: str, *, actor: str, now: datetime) -> dict:
    """RNT-05: đủ hai biên bản, không còn phụ phí PENDING. Không bắt buộc đã thu đủ tiền."""
    require_tx(conn)
    row = rental_row(conn, code)
    _require_status(row, "RETURNED", "được hoàn tất")
    if _inspection(conn, code, HANDOVER) is None or _inspection(conn, code, RETURN) is None:
        raise Conflict("Cần đủ biên bản bàn giao và nhận lại.", code="EVIDENCE_REQUIRED")
    pending = integrations.hooks.pending_charge_count(conn, row["order_code"])
    if pending:
        raise Conflict(f"Còn {pending} đề nghị phụ phí chờ duyệt. Xử lý trước khi hoàn tất.",
                       code="PENDING_CHARGES", details={"pending_charges": pending})
    _set_status(conn, code, "COMPLETED", actor=actor, now=now)
    integrations.hooks.operation_completed(conn, row["order_code"], RENTAL, now)
    write_audit(conn, actor=actor, action="RENTAL_COMPLETE", object_type="RentalRecord", object_code=code, at=now,
                before={"status": "RETURNED"}, after={"status": "COMPLETED"})
    return get_rental_detail(conn, code, now)


def add_note(conn, code: str, *, note: str, actor: str, now: datetime) -> dict:
    """Ghi chú bổ sung cho biên bản đã chốt; không sửa nội dung cũ."""
    require_tx(conn)
    rental_row(conn, code)
    note = clean_text(note, "note", max_len=NOTE_MAX)
    conn.execute("INSERT INTO record_notes(owner_type, owner_code, note, author, created_at) "
                 "VALUES ('RENTAL', ?, ?, ?, ?)", (code, note, actor, to_db(now)))
    return get_rental_detail(conn, code, now)
