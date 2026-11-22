"""Thực hiện chuyến vận tải: nhận hàng, vận chuyển, giao hàng, hoàn tất, sự cố (TRN-03/04/05).

Luồng: ASSIGNED → PICKED_UP → IN_TRANSIT → DELIVERED → COMPLETED. Không bỏ bước.
Điều phối nhập thông tin tài xế báo về. Mỗi mốc lưu thời điểm thực tế và thời điểm nhập.
Trạng thái chỉ đổi theo sự kiện được ghi nhận, không tự suy từ giờ dự kiến (TRN-05).
"""

import json
import sqlite3
from datetime import datetime

from . import attachments, booking, integrations
from .assignments import trip_row, trip_to_dict
from .audit import write_audit
from .booking import TRANSPORT
from .db import next_code, require_tx
from .errors import Conflict, NotFound, ValidationFailed
from .timeutil import from_db, to_db, to_utc
from .validation import NAME_MAX, NOTE_MAX, check_version, clean_text

INCIDENT_TYPES = ("DELAY", "BREAKDOWN", "DAMAGE", "DELIVERY_FAILED", "OTHER")
DELIVERY_EVIDENCE = "TRIP_DELIVERY"
_NOT_OVERDUE = ("DELIVERED", "COMPLETED", "CANCELLED")


def _actual(value: datetime, field: str, now: datetime, after: str | None = None,
            after_label: str = "mốc trước") -> str:
    """Thời điểm thực tế: không ở tương lai, không trước mốc thực tế trước đó (SRS mục 5)."""
    moment = to_utc(value)
    if moment > to_utc(now):
        raise ValidationFailed("Thời điểm thực tế không được ở tương lai.", details={"field": field})
    if after is not None and moment < from_db(after):
        raise ValidationFailed(f"Thời điểm thực tế không được trước {after_label}.", details={"field": field})
    return to_db(moment)


def _milestones(conn, code: str) -> list[dict]:
    rows = conn.execute("SELECT * FROM trip_milestones WHERE trip_code = ? ORDER BY id", (code,)).fetchall()
    return [{
        "milestone": r["milestone"], "actual_at": r["actual_at"], "recorded_at": r["recorded_at"],
        "recorded_by": r["recorded_by"], "person_name": r["person_name"], "note": r["note"],
        "details": json.loads(r["details_json"]) if r["details_json"] else {},
    } for r in rows]


def _milestone_at(conn, code: str, milestone: str) -> str | None:
    row = conn.execute("SELECT actual_at FROM trip_milestones WHERE trip_code = ? AND milestone = ?",
                       (code, milestone)).fetchone()
    return row["actual_at"] if row else None


def _incident_to_dict(r: sqlite3.Row) -> dict:
    return {k: r[k] for k in ("code", "trip_code", "incident_type", "occurred_at", "description", "handler_name",
                              "expected_resolution_at", "status", "resolution", "resolved_at", "resolved_by",
                              "version")}


def _overdue(row: sqlite3.Row, trip_booking: dict | None, now: datetime) -> bool:
    """Cờ quá hạn vận hành: đã qua giờ kết thúc cam kết mà chưa giao xong (TRN-05)."""
    if row["status"] in _NOT_OVERDUE:
        return False
    deadline = row["committed_end"] or (trip_booking["planned_end"] if trip_booking else None)
    return deadline is not None and to_utc(now) > from_db(deadline)


def get_trip_detail(conn, code: str, now: datetime) -> dict:
    row = trip_row(conn, code)
    detail = trip_to_dict(conn, row, now)
    incidents = [_incident_to_dict(r) for r in conn.execute(
        "SELECT * FROM incidents WHERE trip_code = ? ORDER BY id", (code,))]
    detail.update({
        "milestones": _milestones(conn, code),
        "incidents": incidents,
        "open_incidents": sum(1 for i in incidents if i["status"] == "OPEN"),
        "delivery_evidence": attachments.list_for(conn, DELIVERY_EVIDENCE, code),
        "overdue": _overdue(row, detail["booking"], now),
    })
    return detail


def list_trips(
    conn, *, now: datetime, status: str | None = None, q: str | None = None, overdue_only: bool = False,
    limit: int = 50, offset: int = 0,
) -> dict:
    where, params = [], []
    if status:
        where.append("t.status = ?")
        params.append(status)
    if q and q.strip():
        where.append("(t.code LIKE ? OR t.order_code LIKE ?)")
        params += [f"%{q.strip().upper()}%"] * 2
    if overdue_only:
        where.append("t.status NOT IN ('DELIVERED', 'COMPLETED', 'CANCELLED') "
                     "AND COALESCE(t.committed_end, b.planned_end) < ?")
        params.append(to_db(now))
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    base = " FROM transport_trips t LEFT JOIN resource_bookings b ON b.id = t.booking_id" + clause
    total = conn.execute("SELECT COUNT(*)" + base, params).fetchone()[0]
    rows = conn.execute("SELECT t.*" + base + " ORDER BY t.id DESC LIMIT ? OFFSET ?",
                        (*params, limit, offset)).fetchall()
    items = []
    for r in rows:
        item = trip_to_dict(conn, r, now)
        item["overdue"] = _overdue(r, item["booking"], now)
        items.append(item)
    return {"items": items, "total": total}


def _record(conn, code: str, milestone: str, actual_s: str, *, actor: str, now: datetime,
            person: str | None = None, note: str | None = None, details: dict | None = None) -> None:
    try:
        conn.execute(
            "INSERT INTO trip_milestones(trip_code, milestone, actual_at, recorded_at, recorded_by, person_name, "
            "note, details_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (code, milestone, actual_s, to_db(now), actor, person, note,
             json.dumps(details, ensure_ascii=False) if details else None))
    except sqlite3.IntegrityError as exc:
        raise Conflict(f"Mốc {milestone} đã được ghi nhận.", code="INVALID_STATE") from exc


def _set_status(conn, code: str, status: str, *, actor: str, now: datetime) -> None:
    conn.execute("UPDATE transport_trips SET status = ?, version = version + 1, updated_at = ?, updated_by = ? "
                 "WHERE code = ?", (status, to_db(now), actor, code))


def _require_status(row: sqlite3.Row, expected: str, action: str) -> None:
    if row["status"] != expected:
        raise Conflict(f"Chỉ chuyến {expected} mới {action}. Chuyến đang {row['status']}.",
                       code="INVALID_STATE", details={"status": row["status"]})


def pickup(
    conn, code: str, *, actual_at: datetime, handed_over_by: str, cargo_condition: str, actor: str, now: datetime,
    actual_weight_kg: float | None = None, discrepancy_note: str | None = None,
) -> dict:
    """TRN-03: ghi nhận nhận hàng. Đơn sang IN_PROGRESS. Vượt tải thì không nhận, giữ ASSIGNED."""
    require_tx(conn)
    row = trip_row(conn, code)
    _require_status(row, "ASSIGNED", "ghi nhận nhận hàng")
    handed_over_by = clean_text(handed_over_by, "handed_over_by", max_len=NAME_MAX)
    cargo_condition = clean_text(cargo_condition, "cargo_condition", max_len=NOTE_MAX)
    discrepancy_note = clean_text(discrepancy_note, "discrepancy_note", max_len=NOTE_MAX, required=False) or None
    actual_s = _actual(actual_at, "actual_at", now)
    current = booking.get_booking(conn, row["booking_id"], now)
    if actual_weight_kg is not None:
        if actual_weight_kg <= 0:
            raise ValidationFailed("Khối lượng thực tế phải lớn hơn 0.", details={"field": "actual_weight_kg"})
        capacity = conn.execute("SELECT capacity_kg FROM vehicles WHERE id = ?",
                                (current["vehicle"]["id"],)).fetchone()["capacity_kg"]
        if actual_weight_kg > capacity:
            raise Conflict(
                f"Khối lượng thực tế {actual_weight_kg:g} kg vượt tải trọng xe {capacity} kg. Chưa nhận hàng; "
                "điều phối cần đổi xe hoặc xử lý lại với kinh doanh.",
                code="OVER_CAPACITY", details={"capacity_kg": capacity, "actual_weight_kg": actual_weight_kg})
    # BR-09: xe/tài xế phải đã xong việc trước; giờ nhận phải nằm trong khung đã giữ.
    booking.start_usage(conn, row["booking_id"], actual_start=from_db(actual_s), actor=actor, now=now)
    _record(conn, code, "PICKED_UP", actual_s, actor=actor, now=now, person=handed_over_by, note=discrepancy_note,
            details={"cargo_condition": cargo_condition, "actual_weight_kg": actual_weight_kg})
    _set_status(conn, code, "PICKED_UP", actor=actor, now=now)
    integrations.hooks.operation_started(conn, row["order_code"], TRANSPORT, from_db(actual_s))
    write_audit(conn, actor=actor, action="TRIP_PICKUP", object_type="TransportTrip", object_code=code, at=now,
                before={"status": "ASSIGNED"}, after={"status": "PICKED_UP", "actual_at": actual_s})
    return get_trip_detail(conn, code, now)


def start_transit(conn, code: str, *, actual_at: datetime, actor: str, now: datetime) -> dict:
    require_tx(conn)
    row = trip_row(conn, code)
    _require_status(row, "PICKED_UP", "chuyển sang đang vận chuyển")
    actual_s = _actual(actual_at, "actual_at", now, _milestone_at(conn, code, "PICKED_UP"), "lúc nhận hàng")
    _record(conn, code, "IN_TRANSIT", actual_s, actor=actor, now=now)
    _set_status(conn, code, "IN_TRANSIT", actor=actor, now=now)
    write_audit(conn, actor=actor, action="TRIP_IN_TRANSIT", object_type="TransportTrip", object_code=code, at=now,
                before={"status": "PICKED_UP"}, after={"status": "IN_TRANSIT", "actual_at": actual_s})
    return get_trip_detail(conn, code, now)


def _insert_incident(conn, code: str, *, incident_type: str, occurred_s: str, description: str, handler: str,
                     expected_resolution_at: datetime | None, actor: str, now: datetime) -> str:
    if incident_type not in INCIDENT_TYPES:
        raise ValidationFailed("Loại sự cố không hợp lệ.", details={"field": "incident_type"})
    inc_code = next_code(conn, "INC")
    now_s = to_db(now)
    conn.execute(
        "INSERT INTO incidents(code, trip_code, incident_type, occurred_at, description, handler_name, "
        "expected_resolution_at, status, created_at, created_by, updated_at, updated_by) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, ?)",
        (inc_code, code, incident_type, occurred_s, description, handler,
         None if expected_resolution_at is None else to_db(expected_resolution_at), now_s, actor, now_s, actor))
    write_audit(conn, actor=actor, action="INCIDENT_OPEN", object_type="Incident", object_code=inc_code, at=now,
                after={"trip_code": code, "incident_type": incident_type})
    return inc_code


def deliver(
    conn, code: str, *, actual_at: datetime, result: str, actor: str, now: datetime,
    receiver_name: str | None = None, note: str | None = None, attachment_codes: list[str] | None = None,
) -> dict:
    """TRN-04: giao hàng. Thành công cần người nhận và ≥1 tệp bằng chứng.
    Không thành công thì giữ IN_TRANSIT và tạo sự cố, không đánh dấu đã giao."""
    require_tx(conn)
    row = trip_row(conn, code)
    _require_status(row, "IN_TRANSIT", "xác nhận giao hàng")
    previous = _milestone_at(conn, code, "IN_TRANSIT")
    actual_s = _actual(actual_at, "actual_at", now, previous, "lúc bắt đầu vận chuyển")
    note = clean_text(note, "note", max_len=NOTE_MAX, required=False) or None
    if result == "FAILED":
        if note is None:
            raise ValidationFailed("Cần mô tả lý do giao không thành công.", details={"field": "note"})
        _insert_incident(conn, code, incident_type="DELIVERY_FAILED", occurred_s=actual_s, description=note,
                         handler=actor, expected_resolution_at=None, actor=actor, now=now)
        return get_trip_detail(conn, code, now)
    if result != "SUCCESS":
        raise ValidationFailed("Kết quả giao phải là SUCCESS hoặc FAILED.", details={"field": "result"})
    receiver_name = clean_text(receiver_name, "receiver_name", max_len=NAME_MAX)
    attachments.link(conn, attachment_codes, owner_type=DELIVERY_EVIDENCE, owner_code=code, actor=actor, now=now)
    _record(conn, code, "DELIVERED", actual_s, actor=actor, now=now, person=receiver_name, note=note,
            details={"result": "SUCCESS"})
    _set_status(conn, code, "DELIVERED", actor=actor, now=now)
    write_audit(conn, actor=actor, action="TRIP_DELIVER", object_type="TransportTrip", object_code=code, at=now,
                before={"status": "IN_TRANSIT"}, after={"status": "DELIVERED", "actual_at": actual_s})
    return get_trip_detail(conn, code, now)


def complete(conn, code: str, *, released_at: datetime, actor: str, now: datetime) -> dict:
    """TRN-04: chốt COMPLETED, giải phóng xe/tài xế tại thời điểm thực tế. Không phụ thuộc thanh toán."""
    require_tx(conn)
    row = trip_row(conn, code)
    _require_status(row, "DELIVERED", "được hoàn tất")
    released_s = _actual(released_at, "released_at", now, _milestone_at(conn, code, "DELIVERED"), "lúc giao hàng")
    if not attachments.list_for(conn, DELIVERY_EVIDENCE, code):
        raise Conflict("Chưa có bằng chứng giao nhận.", code="EVIDENCE_REQUIRED")
    pending = integrations.hooks.pending_charge_count(conn, row["order_code"])
    if pending:
        raise Conflict(f"Còn {pending} đề nghị phụ phí chờ duyệt. Xử lý trước khi hoàn tất.",
                       code="PENDING_CHARGES", details={"pending_charges": pending})
    booking.release(conn, row["booking_id"], reason="Hoàn tất chuyến", actual_end=from_db(released_s),
                    actor=actor, now=now)
    _record(conn, code, "COMPLETED", released_s, actor=actor, now=now)
    _set_status(conn, code, "COMPLETED", actor=actor, now=now)
    integrations.hooks.operation_completed(conn, row["order_code"], TRANSPORT, now)
    write_audit(conn, actor=actor, action="TRIP_COMPLETE", object_type="TransportTrip", object_code=code, at=now,
                before={"status": "DELIVERED"}, after={"status": "COMPLETED", "released_at": released_s})
    return get_trip_detail(conn, code, now)


def create_incident(
    conn, code: str, *, incident_type: str, occurred_at: datetime, description: str, handler_name: str,
    actor: str, now: datetime, expected_resolution_at: datetime | None = None,
) -> dict:
    """TRN-05: ghi sự cố. Không tự đổi trạng thái chuyến hay giá; phụ phí theo SAL-08."""
    require_tx(conn)
    row = trip_row(conn, code)
    if row["status"] in ("COMPLETED", "CANCELLED"):
        raise Conflict("Chuyến đã kết thúc, không ghi thêm sự cố.", code="INVALID_STATE",
                       details={"status": row["status"]})
    _insert_incident(
        conn, code, incident_type=incident_type, occurred_s=_actual(occurred_at, "occurred_at", now),
        description=clean_text(description, "description", max_len=NOTE_MAX),
        handler=clean_text(handler_name, "handler_name", max_len=NAME_MAX),
        expected_resolution_at=expected_resolution_at, actor=actor, now=now)
    return get_trip_detail(conn, code, now)


def resolve_incident(
    conn, incident_code: str, *, expected_version: int, resolution: str, resolved_at: datetime, actor: str,
    now: datetime,
) -> dict:
    require_tx(conn)
    row = conn.execute("SELECT * FROM incidents WHERE code = ?", (incident_code,)).fetchone()
    if row is None:
        raise NotFound("Không tìm thấy sự cố.")
    check_version(row["version"], expected_version)
    if row["status"] != "OPEN":
        raise Conflict("Sự cố đã được đóng.", code="INVALID_STATE")
    resolution = clean_text(resolution, "resolution", max_len=NOTE_MAX)
    resolved_s = _actual(resolved_at, "resolved_at", now, row["occurred_at"], "lúc xảy ra sự cố")
    conn.execute(
        "UPDATE incidents SET status = 'RESOLVED', resolution = ?, resolved_at = ?, resolved_by = ?, "
        "version = version + 1, updated_at = ?, updated_by = ? WHERE id = ?",
        (resolution, resolved_s, actor, to_db(now), actor, row["id"]))
    write_audit(conn, actor=actor, action="INCIDENT_RESOLVE", object_type="Incident", object_code=incident_code,
                at=now, before={"status": "OPEN"}, after={"status": "RESOLVED"}, reason=resolution)
    return get_trip_detail(conn, row["trip_code"], now)
