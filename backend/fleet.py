"""Quản lý xe và tài xế (TRN-01) và tra cứu nguồn lực còn trống (TRN-02, RNT-01)."""

import re
import sqlite3
from datetime import datetime

from . import booking
from .audit import write_audit
from .db import next_code, require_tx
from .errors import Conflict, NotFound, ValidationFailed
from .timeutil import to_db, to_utc
from .validation import NAME_MAX, NOTE_MAX, check_version, clean_text

VEHICLE_STATUSES = ("ACTIVE", "MAINTENANCE", "INACTIVE")
DRIVER_STATUSES = ("ACTIVE", "INACTIVE")
_PLATE_SEPARATORS = re.compile(r"[\s\-._]")
_PLATE_KEY = re.compile(r"^[A-Z0-9]{5,12}$")
_PHONE_SEPARATORS = re.compile(r"[\s\-.()]")
_PHONE = re.compile(r"^\+?\d{8,15}$")
TYPE_MAX = 50


def normalize_plate(raw: str) -> tuple[str, str]:
    """Trả (biển số hiển thị, khóa so trùng). '51c-123.45' và '51C 12345' cùng khóa."""
    display = " ".join((raw or "").split()).upper()
    key = _PLATE_SEPARATORS.sub("", display)
    if not _PLATE_KEY.match(key):
        raise ValidationFailed(
            "Biển số chỉ gồm chữ và số, 5–12 ký tự sau khi bỏ dấu cách và dấu phân cách.",
            details={"field": "plate_number"},
        )
    return display, key


def normalize_phone(raw: str) -> str:
    phone = _PHONE_SEPARATORS.sub("", (raw or "").strip())
    if not _PHONE.match(phone):
        raise ValidationFailed(
            "Số điện thoại gồm 8–15 chữ số, có thể có dấu + ở đầu.", details={"field": "phone"}
        )
    return phone


def _check_capacity(capacity_kg: int) -> int:
    if capacity_kg is None or capacity_kg <= 0:
        raise ValidationFailed("Tải trọng phải lớn hơn 0.", details={"field": "capacity_kg"})
    return capacity_kg


def _in_use_sql(column: str) -> str:
    return (f"EXISTS (SELECT 1 FROM resource_bookings b WHERE b.{column} = x.id "
            "AND b.status = 'IN_USE') AS in_use")


def vehicle_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "plate_number": row["plate_number"],
        "vehicle_type": row["vehicle_type"],
        "capacity_kg": row["capacity_kg"],
        "description": row["description"],
        "status": row["status"],
        "in_use": bool(row["in_use"]),
        "version": row["version"],
        "updated_at": row["updated_at"],
    }


def driver_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "code": row["code"],
        "full_name": row["full_name"],
        "phone": row["phone"],
        "status": row["status"],
        "in_use": bool(row["in_use"]),
        "version": row["version"],
        "updated_at": row["updated_at"],
    }


def _vehicle_row(conn, vehicle_id: int) -> sqlite3.Row:
    row = conn.execute(
        f"SELECT x.*, {_in_use_sql('vehicle_id')} FROM vehicles x WHERE x.id = ?", (vehicle_id,)
    ).fetchone()
    if row is None:
        raise NotFound("Không tìm thấy xe.")
    return row


def _driver_row(conn, driver_id: int) -> sqlite3.Row:
    row = conn.execute(
        f"SELECT x.*, {_in_use_sql('driver_id')} FROM drivers x WHERE x.id = ?", (driver_id,)
    ).fetchone()
    if row is None:
        raise NotFound("Không tìm thấy tài xế.")
    return row


def get_vehicle(conn, vehicle_id: int) -> dict:
    return vehicle_to_dict(_vehicle_row(conn, vehicle_id))


def get_driver(conn, driver_id: int) -> dict:
    return driver_to_dict(_driver_row(conn, driver_id))


def _vehicle_audit(row) -> dict:
    return {k: row[k] for k in ("plate_number", "vehicle_type", "capacity_kg", "description", "status")}


def _ensure_plate_free(conn, key: str, exclude_id: int | None = None) -> None:
    other = conn.execute(
        "SELECT id, plate_number FROM vehicles WHERE plate_key = ? AND id IS NOT ?",
        (key, exclude_id),
    ).fetchone()
    if other is not None:
        raise Conflict(
            f"Biển số đã tồn tại ({other['plate_number']}).",
            code="DUPLICATE_PLATE",
            details={"field": "plate_number", "vehicle_id": other["id"]},
        )


def create_vehicle(
    conn: sqlite3.Connection,
    *,
    plate_number: str,
    vehicle_type: str,
    capacity_kg: int,
    description: str = "",
    status: str = "ACTIVE",
    actor: str,
    now: datetime,
) -> dict:
    require_tx(conn)
    display, key = normalize_plate(plate_number)
    vehicle_type = clean_text(vehicle_type, "vehicle_type", max_len=TYPE_MAX)
    _check_capacity(capacity_kg)
    description = clean_text(description, "description", max_len=NOTE_MAX, required=False)
    if status not in VEHICLE_STATUSES:
        raise ValidationFailed("Trạng thái xe không hợp lệ.", details={"field": "status"})
    _ensure_plate_free(conn, key)
    now_s = to_db(now)
    cur = conn.execute(
        "INSERT INTO vehicles(plate_number, plate_key, vehicle_type, capacity_kg, description, status, "
        "created_at, created_by, updated_at, updated_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (display, key, vehicle_type, capacity_kg, description, status, now_s, actor, now_s, actor),
    )
    row = _vehicle_row(conn, cur.lastrowid)
    write_audit(conn, actor=actor, action="VEHICLE_CREATE", object_type="Vehicle",
                object_code=display, at=now, after=_vehicle_audit(row))
    return vehicle_to_dict(row)


def update_vehicle(
    conn: sqlite3.Connection,
    vehicle_id: int,
    *,
    expected_version: int,
    actor: str,
    now: datetime,
    plate_number: str | None = None,
    vehicle_type: str | None = None,
    capacity_kg: int | None = None,
    description: str | None = None,
) -> dict:
    require_tx(conn)
    row = _vehicle_row(conn, vehicle_id)
    check_version(row["version"], expected_version)
    display, key = row["plate_number"], row["plate_key"]
    if plate_number is not None:
        display, key = normalize_plate(plate_number)
        _ensure_plate_free(conn, key, exclude_id=vehicle_id)
    new_type = row["vehicle_type"] if vehicle_type is None else clean_text(
        vehicle_type, "vehicle_type", max_len=TYPE_MAX)
    new_capacity = row["capacity_kg"] if capacity_kg is None else _check_capacity(capacity_kg)
    new_description = row["description"] if description is None else clean_text(
        description, "description", max_len=NOTE_MAX, required=False)

    # Không để sửa hồ sơ xe làm các lịch đã giữ trở nên sai điều kiện BR-08.
    if new_capacity < row["capacity_kg"]:
        too_heavy = conn.execute(
            "SELECT b.code, b.source_code, t.cargo_weight_kg AS need FROM resource_bookings b "
            "JOIN transport_trips t ON b.source_type = 'TRANSPORT' AND t.code = b.source_code "
            "WHERE b.vehicle_id = ? AND b.status <> 'RELEASED' AND t.cargo_weight_kg > ? "
            "UNION ALL "
            "SELECT b.code, b.source_code, r.min_capacity_kg AS need FROM resource_bookings b "
            "JOIN rental_records r ON b.source_type = 'RENTAL' AND r.code = b.source_code "
            "WHERE b.vehicle_id = ? AND b.status <> 'RELEASED' AND r.min_capacity_kg > ?",
            (vehicle_id, new_capacity, vehicle_id, new_capacity),
        ).fetchall()
        if too_heavy:
            raise Conflict(
                "Tải trọng mới nhỏ hơn yêu cầu của các lịch đã giữ. Đổi xe cho các lịch này trước.",
                code="CAPACITY_BELOW_BOOKINGS",
                details={"bookings": [dict(r) for r in too_heavy]},
            )
    if new_type != row["vehicle_type"]:
        mismatched = conn.execute(
            "SELECT b.code, b.source_code FROM resource_bookings b "
            "JOIN rental_records r ON b.source_type = 'RENTAL' AND r.code = b.source_code "
            "WHERE b.vehicle_id = ? AND b.status <> 'RELEASED' AND r.vehicle_type <> ?",
            (vehicle_id, new_type),
        ).fetchall()
        if mismatched:
            raise Conflict(
                "Các hồ sơ thuê đã giữ xe này yêu cầu loại xe cũ. Đổi xe cho các lịch này trước.",
                code="VEHICLE_TYPE_USED_BY_BOOKINGS",
                details={"bookings": [dict(r) for r in mismatched]},
            )

    conn.execute(
        "UPDATE vehicles SET plate_number = ?, plate_key = ?, vehicle_type = ?, capacity_kg = ?, "
        "description = ?, version = version + 1, updated_at = ?, updated_by = ? WHERE id = ?",
        (display, key, new_type, new_capacity, new_description, to_db(now), actor, vehicle_id),
    )
    after = _vehicle_row(conn, vehicle_id)
    write_audit(conn, actor=actor, action="VEHICLE_UPDATE", object_type="Vehicle",
                object_code=after["plate_number"], at=now, before=_vehicle_audit(row),
                after=_vehicle_audit(after))
    return vehicle_to_dict(after)


def _guard_status_change(conn, *, vehicle_id=None, driver_id=None, label: str) -> None:
    in_use, held = booking.blocking_bookings(conn, vehicle_id=vehicle_id, driver_id=driver_id)
    if in_use:
        raise Conflict(
            f"{label} đang được sử dụng. Kết thúc chuyến hoặc nhận lại xe trước khi đổi trạng thái.",
            code="RESOURCE_IN_USE",
            details={"bookings": in_use},
        )
    if held:
        raise Conflict(
            f"{label} còn lịch chưa thực hiện. Gỡ hoặc đổi các phân công này trước.",
            code="FUTURE_BOOKINGS_EXIST",
            details={"bookings": held},
        )


def set_vehicle_status(
    conn: sqlite3.Connection,
    vehicle_id: int,
    *,
    status: str,
    expected_version: int,
    actor: str,
    now: datetime,
    reason: str | None = None,
) -> dict:
    require_tx(conn)
    if status not in VEHICLE_STATUSES:
        raise ValidationFailed("Trạng thái xe không hợp lệ.", details={"field": "status"})
    reason = clean_text(reason, "reason", max_len=NOTE_MAX, required=False) or None
    row = _vehicle_row(conn, vehicle_id)
    check_version(row["version"], expected_version)
    if status == row["status"]:
        return vehicle_to_dict(row)
    if status != "ACTIVE":
        _guard_status_change(conn, vehicle_id=vehicle_id, label=f"Xe {row['plate_number']}")
    conn.execute(
        "UPDATE vehicles SET status = ?, version = version + 1, updated_at = ?, updated_by = ? "
        "WHERE id = ?",
        (status, to_db(now), actor, vehicle_id),
    )
    after = _vehicle_row(conn, vehicle_id)
    write_audit(conn, actor=actor, action="VEHICLE_STATUS", object_type="Vehicle",
                object_code=row["plate_number"], at=now, before={"status": row["status"]},
                after={"status": status}, reason=reason)
    return vehicle_to_dict(after)


def list_vehicles(
    conn: sqlite3.Connection, *, status: str | None = None, q: str | None = None,
    limit: int = 50, offset: int = 0,
) -> dict:
    where, params = [], []
    if status:
        where.append("x.status = ?")
        params.append(status)
    if q and q.strip():
        where.append("(x.plate_key LIKE ? OR x.vehicle_type LIKE ?)")
        needle = _PLATE_SEPARATORS.sub("", q.strip().upper())
        params += [f"%{needle}%", f"%{q.strip()}%"]
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    total = conn.execute("SELECT COUNT(*) FROM vehicles x" + clause, params).fetchone()[0]
    rows = conn.execute(
        f"SELECT x.*, {_in_use_sql('vehicle_id')} FROM vehicles x{clause} "
        "ORDER BY x.plate_key LIMIT ? OFFSET ?",
        (*params, limit, offset),
    ).fetchall()
    return {"items": [vehicle_to_dict(r) for r in rows], "total": total}


def _driver_audit(row) -> dict:
    return {k: row[k] for k in ("code", "full_name", "phone", "status")}


def create_driver(
    conn: sqlite3.Connection, *, full_name: str, phone: str, status: str = "ACTIVE", actor: str,
    now: datetime,
) -> dict:
    require_tx(conn)
    full_name = clean_text(full_name, "full_name", max_len=NAME_MAX)
    phone = normalize_phone(phone)
    if status not in DRIVER_STATUSES:
        raise ValidationFailed("Trạng thái tài xế không hợp lệ.", details={"field": "status"})
    code = next_code(conn, "DRV", width=4)
    now_s = to_db(now)
    cur = conn.execute(
        "INSERT INTO drivers(code, full_name, phone, status, created_at, created_by, updated_at, "
        "updated_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (code, full_name, phone, status, now_s, actor, now_s, actor),
    )
    row = _driver_row(conn, cur.lastrowid)
    write_audit(conn, actor=actor, action="DRIVER_CREATE", object_type="Driver", object_code=code,
                at=now, after=_driver_audit(row))
    return driver_to_dict(row)


def update_driver(
    conn: sqlite3.Connection, driver_id: int, *, expected_version: int, actor: str, now: datetime,
    full_name: str | None = None, phone: str | None = None,
) -> dict:
    require_tx(conn)
    row = _driver_row(conn, driver_id)
    check_version(row["version"], expected_version)
    new_name = row["full_name"] if full_name is None else clean_text(full_name, "full_name", max_len=NAME_MAX)
    new_phone = row["phone"] if phone is None else normalize_phone(phone)
    conn.execute(
        "UPDATE drivers SET full_name = ?, phone = ?, version = version + 1, updated_at = ?, "
        "updated_by = ? WHERE id = ?",
        (new_name, new_phone, to_db(now), actor, driver_id),
    )
    after = _driver_row(conn, driver_id)
    write_audit(conn, actor=actor, action="DRIVER_UPDATE", object_type="Driver",
                object_code=row["code"], at=now, before=_driver_audit(row), after=_driver_audit(after))
    return driver_to_dict(after)


def set_driver_status(
    conn: sqlite3.Connection, driver_id: int, *, status: str, expected_version: int, actor: str,
    now: datetime, reason: str | None = None,
) -> dict:
    require_tx(conn)
    if status not in DRIVER_STATUSES:
        raise ValidationFailed("Trạng thái tài xế không hợp lệ.", details={"field": "status"})
    reason = clean_text(reason, "reason", max_len=NOTE_MAX, required=False) or None
    row = _driver_row(conn, driver_id)
    check_version(row["version"], expected_version)
    if status == row["status"]:
        return driver_to_dict(row)
    if status == "INACTIVE":
        _guard_status_change(conn, driver_id=driver_id, label=f"Tài xế {row['code']}")
    conn.execute(
        "UPDATE drivers SET status = ?, version = version + 1, updated_at = ?, updated_by = ? "
        "WHERE id = ?",
        (status, to_db(now), actor, driver_id),
    )
    after = _driver_row(conn, driver_id)
    write_audit(conn, actor=actor, action="DRIVER_STATUS", object_type="Driver",
                object_code=row["code"], at=now, before={"status": row["status"]},
                after={"status": status}, reason=reason)
    return driver_to_dict(after)


def list_drivers(
    conn: sqlite3.Connection, *, status: str | None = None, q: str | None = None,
    limit: int = 50, offset: int = 0,
) -> dict:
    where, params = [], []
    if status:
        where.append("x.status = ?")
        params.append(status)
    if q and q.strip():
        where.append("(x.code LIKE ? OR x.full_name LIKE ?)")
        params += [f"%{q.strip().upper()}%", f"%{q.strip()}%"]
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    total = conn.execute("SELECT COUNT(*) FROM drivers x" + clause, params).fetchone()[0]
    rows = conn.execute(
        f"SELECT x.*, {_in_use_sql('driver_id')} FROM drivers x{clause} ORDER BY x.code LIMIT ? OFFSET ?",
        (*params, limit, offset),
    ).fetchall()
    return {"items": [driver_to_dict(r) for r in rows], "total": total}


def _window(start: datetime, end: datetime) -> tuple[str, str]:
    start_u, end_u = to_utc(start), to_utc(end)
    if end_u <= start_u:
        raise ValidationFailed("Thời điểm kết thúc phải sau thời điểm bắt đầu.", details={"field": "end"})
    return to_db(start_u), to_db(end_u)


def available_vehicles(
    conn: sqlite3.Connection, *, start: datetime, end: datetime, min_capacity_kg: float = 0,
    vehicle_type: str | None = None,
) -> list[dict]:
    """Xe ACTIVE, đủ tải, không chồng lịch vận tải lẫn thuê xe (BR-07/08).

    in_use=True nghĩa là xe đang ở ngoài với việc khác; nếu việc đó trễ thì vẫn chưa
    bắt đầu được việc mới (BR-09), nên FE nên hiển thị cảnh báo.
    """
    start_s, end_s = _window(start, end)
    sql = (
        f"SELECT x.*, {_in_use_sql('vehicle_id')} FROM vehicles x "
        "WHERE x.status = 'ACTIVE' AND x.capacity_kg >= ? "
        "AND NOT EXISTS (SELECT 1 FROM resource_bookings b WHERE b.vehicle_id = x.id "
        "AND b.status <> 'RELEASED' AND b.planned_start < ? AND ? < b.planned_end)"
    )
    params: list = [min_capacity_kg, end_s, start_s]
    if vehicle_type:
        sql += " AND x.vehicle_type = ?"
        params.append(vehicle_type.strip())
    rows = conn.execute(sql + " ORDER BY x.capacity_kg, x.plate_key", params).fetchall()
    return [vehicle_to_dict(r) for r in rows]


def available_drivers(conn: sqlite3.Connection, *, start: datetime, end: datetime) -> list[dict]:
    start_s, end_s = _window(start, end)
    rows = conn.execute(
        f"SELECT x.*, {_in_use_sql('driver_id')} FROM drivers x WHERE x.status = 'ACTIVE' "
        "AND NOT EXISTS (SELECT 1 FROM resource_bookings b WHERE b.driver_id = x.id "
        "AND b.status <> 'RELEASED' AND b.planned_start < ? AND ? < b.planned_end) ORDER BY x.code",
        (end_s, start_s),
    ).fetchall()
    return [driver_to_dict(r) for r in rows]
