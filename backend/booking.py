"""Engine giữ lịch xe và tài xế, dùng chung cho vận tải và thuê xe.

Quy tắc chính:
- BR-07: lịch là khoảng [bắt đầu, kết thúc). Hai lịch liền nhau 08:00–10:00 và
  10:00–12:00 không chồng nhau.
- BR-08: chỉ xe ACTIVE, đủ tải trọng và tài xế ACTIVE mới được giữ.
- BR-09: xe/tài xế đang dùng thực tế (IN_USE) chặn bắt đầu việc tiếp theo, kể cả khi
  đã quá giờ kết thúc dự kiến. Kéo dài lịch mà chồng lịch sau thì báo xung đột,
  không ghi đè.

Mọi hàm ghi đều phải chạy bên trong write_tx() của người gọi, để người gọi gộp được
nhiều thao tác (ví dụ hủy đơn ở SAL-09) vào cùng một transaction.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from .audit import write_audit
from .db import next_code, require_tx
from .errors import BookingConflict, Conflict, NotFound, ValidationFailed
from .timeutil import PLANNED_START_GRACE, from_db, to_db, to_utc
from .validation import NOTE_MAX, clean_text

TRANSPORT = "TRANSPORT"
RENTAL = "RENTAL"
HELD = "HELD"
IN_USE = "IN_USE"
RELEASED = "RELEASED"


@dataclass(frozen=True)
class BookingRequest:
    source_type: str
    source_code: str
    vehicle_id: int
    driver_id: int | None
    planned_start: datetime
    planned_end: datetime
    required_capacity_kg: float
    required_vehicle_type: str | None = None


_SELECT = """
SELECT b.*, v.plate_number, v.vehicle_type,
       d.code AS driver_code, d.full_name AS driver_name
FROM resource_bookings b
JOIN vehicles v ON v.id = b.vehicle_id
LEFT JOIN drivers d ON d.id = b.driver_id
"""


def booking_to_dict(row: sqlite3.Row, now: datetime) -> dict:
    planned_end = from_db(row["planned_end"])
    return {
        "id": row["id"],
        "code": row["code"],
        "source_type": row["source_type"],
        "source_code": row["source_code"],
        "vehicle": {
            "id": row["vehicle_id"],
            "plate_number": row["plate_number"],
            "vehicle_type": row["vehicle_type"],
        },
        "driver": None
        if row["driver_id"] is None
        else {"id": row["driver_id"], "code": row["driver_code"], "full_name": row["driver_name"]},
        "planned_start": row["planned_start"],
        "planned_end": row["planned_end"],
        "status": row["status"],
        "actual_start": row["actual_start"],
        "actual_end": row["actual_end"],
        "release_reason": row["release_reason"],
        "version": row["version"],
        # Cờ quá hạn: đang dùng mà đã qua giờ kết thúc dự kiến (TRN-05, RNT-04).
        "overdue": row["status"] == IN_USE and to_utc(now) > planned_end,
    }


def _snapshot(row: sqlite3.Row) -> dict:
    keys = ("code", "vehicle_id", "driver_id", "planned_start", "planned_end", "status",
            "actual_start", "actual_end")
    return {k: row[k] for k in keys}


def _row(conn: sqlite3.Connection, booking_id: int) -> sqlite3.Row:
    row = conn.execute(_SELECT + " WHERE b.id = ?", (booking_id,)).fetchone()
    if row is None:
        raise NotFound("Không tìm thấy lịch giữ.")
    return row


def get_booking(conn: sqlite3.Connection, booking_id: int, now: datetime) -> dict:
    return booking_to_dict(_row(conn, booking_id), now)


def _conflict_item(row: sqlite3.Row, resource: str) -> dict:
    return {
        "resource": resource,
        "booking_code": row["code"],
        "source_type": row["source_type"],
        "source_code": row["source_code"],
        "planned_start": row["planned_start"],
        "planned_end": row["planned_end"],
        "status": row["status"],
    }


def find_conflicts(
    conn: sqlite3.Connection,
    *,
    vehicle_id: int,
    driver_id: int | None,
    start: str,
    end: str,
    exclude_ids: tuple[int, ...] = (),
) -> list[dict]:
    """Các lịch còn hiệu lực chồng lên [start, end) của xe hoặc tài xế."""
    exclude_sql = ""
    if exclude_ids:
        exclude_sql = f" AND id NOT IN ({','.join('?' * len(exclude_ids))})"
    overlap = " AND status <> 'RELEASED' AND planned_start < ? AND ? < planned_end"
    items = [
        _conflict_item(r, "VEHICLE")
        for r in conn.execute(
            "SELECT * FROM resource_bookings WHERE vehicle_id = ?" + overlap + exclude_sql
            + " ORDER BY planned_start",
            (vehicle_id, end, start, *exclude_ids),
        )
    ]
    if driver_id is not None:
        items += [
            _conflict_item(r, "DRIVER")
            for r in conn.execute(
                "SELECT * FROM resource_bookings WHERE driver_id = ?" + overlap + exclude_sql
                + " ORDER BY planned_start",
                (driver_id, end, start, *exclude_ids),
            )
        ]
    return items


def blocking_bookings(
    conn: sqlite3.Connection, *, vehicle_id: int | None = None, driver_id: int | None = None
) -> tuple[list[dict], list[dict]]:
    """(đang dùng, đã giữ chưa dùng) của một xe hoặc tài xế. Dùng cho TRN-01."""
    column, value = ("vehicle_id", vehicle_id) if vehicle_id is not None else ("driver_id", driver_id)
    resource = "VEHICLE" if vehicle_id is not None else "DRIVER"
    rows = conn.execute(
        f"SELECT * FROM resource_bookings WHERE {column} = ? AND status <> 'RELEASED' "
        "ORDER BY planned_start",
        (value,),
    ).fetchall()
    in_use = [_conflict_item(r, resource) for r in rows if r["status"] == IN_USE]
    held = [_conflict_item(r, resource) for r in rows if r["status"] == HELD]
    return in_use, held


def validate_window(start: datetime, end: datetime, now: datetime) -> tuple[datetime, datetime]:
    start_u, end_u = to_utc(start), to_utc(end)
    if end_u <= start_u:
        raise ValidationFailed(
            "Thời điểm kết thúc phải sau thời điểm bắt đầu.", details={"field": "planned_end"}
        )
    if start_u < to_utc(now) - PLANNED_START_GRACE:
        raise ValidationFailed(
            "Thời điểm bắt đầu dự kiến phải ở hiện tại hoặc tương lai.",
            details={"field": "planned_start"},
        )
    return start_u, end_u


def _check_vehicle(conn, vehicle_id: int, capacity_kg: float, vehicle_type: str | None):
    v = conn.execute("SELECT * FROM vehicles WHERE id = ?", (vehicle_id,)).fetchone()
    if v is None:
        raise NotFound("Không tìm thấy xe.", details={"field": "vehicle_id"})
    if v["status"] != "ACTIVE":
        raise Conflict(
            f"Xe {v['plate_number']} đang ở trạng thái {v['status']}, không thể giữ hoặc phân công.",
            code="VEHICLE_NOT_ELIGIBLE",
            details={"vehicle_status": v["status"]},
        )
    if v["capacity_kg"] < capacity_kg:
        raise Conflict(
            f"Xe {v['plate_number']} tải trọng {v['capacity_kg']} kg, không đủ cho {capacity_kg:g} kg.",
            code="CAPACITY_INSUFFICIENT",
            details={"capacity_kg": v["capacity_kg"], "required_kg": capacity_kg},
        )
    if vehicle_type is not None and v["vehicle_type"] != vehicle_type:
        raise Conflict(
            f"Xe {v['plate_number']} thuộc loại {v['vehicle_type']}, không phải {vehicle_type}.",
            code="VEHICLE_TYPE_MISMATCH",
            details={"vehicle_type": v["vehicle_type"], "required_type": vehicle_type},
        )
    return v


def _check_driver(conn, driver_id: int):
    d = conn.execute("SELECT * FROM drivers WHERE id = ?", (driver_id,)).fetchone()
    if d is None:
        raise NotFound("Không tìm thấy tài xế.", details={"field": "driver_id"})
    if d["status"] != "ACTIVE":
        raise Conflict(
            f"Tài xế {d['code']} đang INACTIVE, không thể phân công.",
            code="DRIVER_NOT_ELIGIBLE",
        )
    return d


def _overlap_error(conn, *, vehicle_id, driver_id, start, end, exclude_ids=()) -> BookingConflict:
    return BookingConflict(
        "Xe hoặc tài xế đã có lịch trong khoảng thời gian này. Kiểm tra lại lịch và chọn nguồn lực khác.",
        details={"conflicts": find_conflicts(
            conn, vehicle_id=vehicle_id, driver_id=driver_id, start=start, end=end,
            exclude_ids=exclude_ids)},
    )


def hold(conn: sqlite3.Connection, req: BookingRequest, *, actor: str, now: datetime) -> dict:
    """Giữ xe (và tài xế nếu là vận tải). Từ chối thì không tạo gì (TRN-02, RNT-01)."""
    require_tx(conn)
    if req.source_type not in (TRANSPORT, RENTAL):
        raise ValueError(f"source_type không hợp lệ: {req.source_type}")
    if req.source_type == TRANSPORT and req.driver_id is None:
        raise ValidationFailed("Chuyến vận tải cần chọn tài xế.", details={"field": "driver_id"})
    if req.source_type == RENTAL and req.driver_id is not None:
        raise ValidationFailed("Thuê xe không kèm tài xế.", details={"field": "driver_id"})
    start_u, end_u = validate_window(req.planned_start, req.planned_end, now)
    _check_vehicle(conn, req.vehicle_id, req.required_capacity_kg, req.required_vehicle_type)
    if req.driver_id is not None:
        _check_driver(conn, req.driver_id)

    existing = conn.execute(
        "SELECT code FROM resource_bookings WHERE source_type = ? AND source_code = ? "
        "AND status <> 'RELEASED'",
        (req.source_type, req.source_code),
    ).fetchone()
    if existing is not None:
        raise Conflict(
            f"{req.source_code} đã có lịch giữ {existing['code']}.",
            code="SOURCE_ALREADY_BOOKED",
            details={"booking_code": existing["code"]},
        )

    start_s, end_s = to_db(start_u), to_db(end_u)
    if find_conflicts(conn, vehicle_id=req.vehicle_id, driver_id=req.driver_id, start=start_s, end=end_s):
        raise _overlap_error(conn, vehicle_id=req.vehicle_id, driver_id=req.driver_id,
                             start=start_s, end=end_s)

    code = next_code(conn, "BK")
    now_s = to_db(now)
    try:
        cur = conn.execute(
            "INSERT INTO resource_bookings(code, source_type, source_code, vehicle_id, driver_id, "
            "planned_start, planned_end, status, created_at, created_by, updated_at, updated_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'HELD', ?, ?, ?, ?)",
            (code, req.source_type, req.source_code, req.vehicle_id, req.driver_id,
             start_s, end_s, now_s, actor, now_s, actor),
        )
    except sqlite3.IntegrityError as exc:
        # Lưới an toàn: trigger/index trong DB bắt được điều code ở trên bỏ sót.
        if "BOOKING_OVERLAP" in str(exc):
            raise _overlap_error(conn, vehicle_id=req.vehicle_id, driver_id=req.driver_id,
                                 start=start_s, end=end_s) from exc
        if "resource_bookings.source_type" in str(exc):
            raise Conflict(f"{req.source_code} đã có lịch giữ.", code="SOURCE_ALREADY_BOOKED") from exc
        raise
    row = _row(conn, cur.lastrowid)
    write_audit(conn, actor=actor, action="BOOKING_HOLD", object_type="ResourceBooking",
                object_code=code, at=now, after=_snapshot(row))
    return booking_to_dict(row, now)


def release(
    conn: sqlite3.Connection,
    booking_id: int,
    *,
    reason: str,
    actor: str,
    now: datetime,
    actual_end: datetime | None = None,
) -> dict:
    """Giải phóng lịch.

    HELD: bỏ giữ trước khi dùng, không có thời điểm thực tế.
    IN_USE: xe/tài xế thực sự được trả; cần thời điểm kết thúc thực tế (BR-09).
    """
    require_tx(conn)
    reason = clean_text(reason, "reason", max_len=NOTE_MAX)
    row = _row(conn, booking_id)
    if row["status"] == RELEASED:
        raise Conflict("Lịch này đã được giải phóng.", code="INVALID_STATE",
                       details={"status": row["status"]})
    actual_end_s = None
    if row["status"] == HELD:
        if actual_end is not None:
            raise ValidationFailed("Lịch chưa được sử dụng nên không có thời điểm kết thúc thực tế.",
                                   details={"field": "actual_end"})
    else:
        if actual_end is None:
            raise ValidationFailed("Cần thời điểm kết thúc thực tế.", details={"field": "actual_end"})
        end_u = to_utc(actual_end)
        if end_u > to_utc(now):
            raise ValidationFailed("Thời điểm thực tế không được ở tương lai.",
                                   details={"field": "actual_end"})
        if end_u < from_db(row["actual_start"]):
            raise ValidationFailed("Thời điểm kết thúc thực tế không được trước lúc bắt đầu thực tế.",
                                   details={"field": "actual_end"})
        actual_end_s = to_db(end_u)
    conn.execute(
        "UPDATE resource_bookings SET status = 'RELEASED', actual_end = ?, release_reason = ?, "
        "version = version + 1, updated_at = ?, updated_by = ? WHERE id = ?",
        (actual_end_s, reason, to_db(now), actor, booking_id),
    )
    after = _row(conn, booking_id)
    write_audit(conn, actor=actor, action="BOOKING_RELEASE", object_type="ResourceBooking",
                object_code=row["code"], at=now, before=_snapshot(row), after=_snapshot(after),
                reason=reason)
    return booking_to_dict(after, now)


def start_usage(
    conn: sqlite3.Connection, booking_id: int, *, actual_start: datetime, actor: str, now: datetime
) -> dict:
    """Bắt đầu dùng thực tế: nhận hàng (TRN-03) hoặc bàn giao xe thuê (RNT-03)."""
    require_tx(conn)
    row = _row(conn, booking_id)
    if row["status"] != HELD:
        raise Conflict("Chỉ bắt đầu được lịch đang giữ.", code="INVALID_STATE",
                       details={"status": row["status"]})
    start_u = to_utc(actual_start)
    if start_u > to_utc(now):
        raise ValidationFailed("Thời điểm thực tế không được ở tương lai.",
                               details={"field": "actual_start"})
    if not (from_db(row["planned_start"]) <= start_u < from_db(row["planned_end"])):
        raise Conflict(
            "Thời điểm bắt đầu nằm ngoài khung giờ đã giữ. Điều chỉnh lịch giữ trước rồi mới bắt đầu.",
            code="OUTSIDE_BOOKED_WINDOW",
            details={"planned_start": row["planned_start"], "planned_end": row["planned_end"]},
        )
    vehicle = conn.execute("SELECT status, plate_number FROM vehicles WHERE id = ?",
                           (row["vehicle_id"],)).fetchone()
    if vehicle["status"] != "ACTIVE":
        raise Conflict(f"Xe {vehicle['plate_number']} đang {vehicle['status']}.",
                       code="VEHICLE_NOT_ELIGIBLE")
    blocking = conn.execute(
        "SELECT * FROM resource_bookings WHERE status = 'IN_USE' AND id <> ? "
        "AND (vehicle_id = ? OR (driver_id IS NOT NULL AND driver_id = ?))",
        (booking_id, row["vehicle_id"], row["driver_id"]),
    ).fetchall()
    if blocking:
        raise Conflict(
            "Xe hoặc tài xế chưa được trả/giải phóng thực tế từ việc trước, chưa thể bắt đầu việc này.",
            code="RESOURCE_STILL_IN_USE",
            details={"blocking": [
                _conflict_item(b, "VEHICLE" if b["vehicle_id"] == row["vehicle_id"] else "DRIVER")
                for b in blocking]},
        )
    try:
        conn.execute(
            "UPDATE resource_bookings SET status = 'IN_USE', actual_start = ?, version = version + 1, "
            "updated_at = ?, updated_by = ? WHERE id = ?",
            (to_db(start_u), to_db(now), actor, booking_id),
        )
    except sqlite3.IntegrityError as exc:
        raise Conflict("Xe hoặc tài xế đang được dùng cho việc khác.",
                       code="RESOURCE_STILL_IN_USE") from exc
    after = _row(conn, booking_id)
    write_audit(conn, actor=actor, action="BOOKING_START", object_type="ResourceBooking",
                object_code=row["code"], at=now, before=_snapshot(row), after=_snapshot(after))
    return booking_to_dict(after, now)


def change_planned_end(
    conn: sqlite3.Connection, booking_id: int, new_end: datetime, *, reason: str, actor: str,
    now: datetime,
) -> dict:
    """Kéo dài hoặc rút ngắn giờ kết thúc dự kiến (TRN-05).

    Nếu chồng lịch tiếp theo: giữ nguyên lịch hiện tại và trả danh sách lịch xung đột
    để điều phối đổi/dời trước (BR-09, NEG-10).
    """
    require_tx(conn)
    reason = clean_text(reason, "reason", max_len=NOTE_MAX)
    row = _row(conn, booking_id)
    if row["status"] not in (HELD, IN_USE):
        raise Conflict("Lịch đã giải phóng, không thể đổi giờ.", code="INVALID_STATE",
                       details={"status": row["status"]})
    end_u = to_utc(new_end)
    if end_u <= from_db(row["planned_start"]):
        raise ValidationFailed("Thời điểm kết thúc phải sau thời điểm bắt đầu.",
                               details={"field": "new_planned_end"})
    if end_u <= to_utc(now):
        raise ValidationFailed("Thời điểm kết thúc dự kiến mới phải ở tương lai.",
                               details={"field": "new_planned_end"})
    end_s = to_db(end_u)
    conflicts = find_conflicts(conn, vehicle_id=row["vehicle_id"], driver_id=row["driver_id"],
                               start=row["planned_start"], end=end_s, exclude_ids=(booking_id,))
    if conflicts:
        raise BookingConflict(
            "Giờ kết thúc mới chồng lên lịch tiếp theo. Lịch hiện tại được giữ nguyên; "
            "cần đổi hoặc dời các lịch bên dưới trước.",
            details={"conflicts": conflicts},
        )
    conn.execute(
        "UPDATE resource_bookings SET planned_end = ?, version = version + 1, updated_at = ?, "
        "updated_by = ? WHERE id = ?",
        (end_s, to_db(now), actor, booking_id),
    )
    after = _row(conn, booking_id)
    write_audit(conn, actor=actor, action="BOOKING_CHANGE_END", object_type="ResourceBooking",
                object_code=row["code"], at=now, before=_snapshot(row), after=_snapshot(after),
                reason=reason)
    return booking_to_dict(after, now)


def replace(
    conn: sqlite3.Connection, booking_id: int, new_req: BookingRequest, *, reason: str, actor: str,
    now: datetime,
) -> dict:
    """Đổi xe/tài xế/khung giờ của lịch chưa dùng. Thất bại thì lịch cũ giữ nguyên (NEG-08)."""
    require_tx(conn)
    old = _row(conn, booking_id)
    if old["status"] != HELD:
        raise Conflict("Chỉ đổi được lịch chưa bắt đầu sử dụng.", code="INVALID_STATE",
                       details={"status": old["status"]})
    if (old["source_type"], old["source_code"]) != (new_req.source_type, new_req.source_code):
        raise ValueError("Lịch mới phải cùng nguồn với lịch cũ")
    conn.execute("SAVEPOINT replace_booking")
    try:
        release(conn, booking_id, reason=reason, actor=actor, now=now)
        new = hold(conn, new_req, actor=actor, now=now)
    except BaseException:
        conn.execute("ROLLBACK TO replace_booking")
        conn.execute("RELEASE replace_booking")
        raise
    conn.execute("RELEASE replace_booking")
    return new


def release_for_source(
    conn: sqlite3.Connection, source_type: str, source_code: str, *, reason: str, actor: str,
    now: datetime,
) -> dict | None:
    """Giải phóng lịch giữ khi hủy đơn (SAL-09). Trả None nếu không có lịch nào."""
    require_tx(conn)
    row = conn.execute(
        "SELECT id, status FROM resource_bookings WHERE source_type = ? AND source_code = ? "
        "AND status <> 'RELEASED'",
        (source_type, source_code),
    ).fetchone()
    if row is None:
        return None
    if row["status"] == IN_USE:
        raise Conflict("Đã bắt đầu sử dụng xe, không thể hủy theo quy trình chuẩn.",
                       code="RESOURCE_IN_USE")
    return release(conn, row["id"], reason=reason, actor=actor, now=now)


def list_schedule(
    conn: sqlite3.Connection,
    *,
    start: datetime,
    end: datetime,
    now: datetime,
    vehicle_id: int | None = None,
    driver_id: int | None = None,
    include_released: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """Lịch xe và tài xế theo khoảng ngày (TRN-02), gồm cả vận tải và thuê xe."""
    start_u, end_u = to_utc(start), to_utc(end)
    if end_u <= start_u:
        raise ValidationFailed("Thời điểm kết thúc phải sau thời điểm bắt đầu.", details={"field": "end"})
    where = ["b.planned_start < ?", "? < b.planned_end"]
    params: list = [to_db(end_u), to_db(start_u)]
    if not include_released:
        where.append("b.status <> 'RELEASED'")
    if vehicle_id is not None:
        where.append("b.vehicle_id = ?")
        params.append(vehicle_id)
    if driver_id is not None:
        where.append("b.driver_id = ?")
        params.append(driver_id)
    clause = " WHERE " + " AND ".join(where)
    total = conn.execute(
        "SELECT COUNT(*) FROM resource_bookings b" + clause, params
    ).fetchone()[0]
    rows = conn.execute(
        _SELECT + clause + " ORDER BY b.planned_start, b.code LIMIT ? OFFSET ?",
        (*params, limit, offset),
    ).fetchall()
    return {"items": [booking_to_dict(r, now) for r in rows], "total": total}
