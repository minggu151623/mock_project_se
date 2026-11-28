"""Phân công chuyến vận tải (TRN-02, TRN-05) và giữ xe cho hồ sơ thuê (RNT-01, RNT-02).

Các hàm create_* và cancel_operational_record là điểm nối cho BE1:
- SAL-06 gọi create_transport_trip / create_rental_record khi sinh đơn.
- SAL-09 gọi cancel_operational_record trong cùng transaction hủy đơn.
"""

import sqlite3
from datetime import datetime

from . import booking, warehouse
from .audit import write_audit
from .booking import RENTAL, TRANSPORT, BookingRequest
from .db import next_code, require_tx
from .errors import Conflict, NotFound, ValidationFailed
from .timeutil import from_db, to_db, to_utc
from .validation import NOTE_MAX, check_version, clean_text

WAREHOUSE = "WAREHOUSE"

# ---------- Chuyến vận tải ----------

def trip_row(conn, code: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM transport_trips WHERE code = ?", (code,)).fetchone()
    if row is None:
        raise NotFound("Không tìm thấy chuyến vận tải.")
    return row


def trip_to_dict(conn, row: sqlite3.Row, now: datetime) -> dict:
    return {
        "code": row["code"],
        "order_code": row["order_code"],
        "customer_code": row["customer_code"],
        "cargo_description": row["cargo_description"],
        "cargo_weight_kg": row["cargo_weight_kg"],
        "pickup_address": row["pickup_address"],
        "delivery_address": row["delivery_address"],
        "pickup_from_warehouse": bool(row["pickup_from_warehouse"]),
        "committed_end": row["committed_end"],
        "status": row["status"],
        "version": row["version"],
        "booking": None if row["booking_id"] is None else booking.get_booking(conn, row["booking_id"], now),
    }


def get_trip(conn, code: str, now: datetime) -> dict:
    return trip_to_dict(conn, trip_row(conn, code), now)


def _save_trip(conn, code: str, *, status: str, booking_id: int | None, actor: str, now: datetime):
    conn.execute(
        "UPDATE transport_trips SET status = ?, booking_id = ?, version = version + 1, "
        "updated_at = ?, updated_by = ? WHERE code = ?",
        (status, booking_id, to_db(now), actor, code),
    )
    return trip_row(conn, code)


def create_transport_trip(
    conn: sqlite3.Connection, *, order_code: str, cargo_weight_kg: float, actor: str, now: datetime,
    customer_code: str = "", cargo_description: str = "", pickup_address: str = "",
    delivery_address: str = "", pickup_from_warehouse: bool = False, committed_end: datetime | None = None,
) -> dict:
    """Một đơn vận tải có đúng một chuyến; gọi lại trả về chuyến đã có (BR-05)."""
    require_tx(conn)
    order_code = clean_text(order_code, "order_code", max_len=50)
    existing = conn.execute("SELECT * FROM transport_trips WHERE order_code = ?", (order_code,)).fetchone()
    if existing is not None:
        return trip_to_dict(conn, existing, now)
    if cargo_weight_kg is None or cargo_weight_kg <= 0:
        raise ValidationFailed("Khối lượng phải lớn hơn 0.", details={"field": "cargo_weight_kg"})
    code = next_code(conn, "TRP")
    now_s = to_db(now)
    conn.execute(
        "INSERT INTO transport_trips(code, order_code, customer_code, cargo_description, cargo_weight_kg, "
        "pickup_address, delivery_address, pickup_from_warehouse, committed_end, status, created_at, "
        "created_by, updated_at, updated_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'UNASSIGNED', ?, ?, ?, ?)",
        (code, order_code, clean_text(customer_code, "customer_code", max_len=50, required=False),
         clean_text(cargo_description, "cargo_description", max_len=NOTE_MAX, required=False),
         cargo_weight_kg, clean_text(pickup_address, "pickup_address", max_len=500, required=False),
         clean_text(delivery_address, "delivery_address", max_len=500, required=False),
         1 if pickup_from_warehouse else 0, None if committed_end is None else to_db(committed_end),
         now_s, actor, now_s, actor),
    )
    write_audit(conn, actor=actor, action="TRIP_CREATE", object_type="TransportTrip", object_code=code,
                at=now, after={"order_code": order_code, "cargo_weight_kg": cargo_weight_kg})
    return get_trip(conn, code, now)


def assign_trip(
    conn: sqlite3.Connection, trip_code: str, *, vehicle_id: int, driver_id: int,
    planned_start: datetime, planned_end: datetime, actor: str, now: datetime,
) -> dict:
    """UNASSIGNED → ASSIGNED, tạo lịch giữ xe và tài xế (TRN-02)."""
    require_tx(conn)
    row = trip_row(conn, trip_code)
    if row["status"] != "UNASSIGNED":
        raise Conflict(
            f"Chuyến đang ở trạng thái {row['status']}; chỉ phân công được chuyến UNASSIGNED. "
            "Tải lại để xem phân công hiện tại.",
            code="INVALID_STATE",
            details={"status": row["status"]},
        )
    new = booking.hold(conn, BookingRequest(
        source_type=TRANSPORT, source_code=trip_code, vehicle_id=vehicle_id, driver_id=driver_id,
        planned_start=planned_start, planned_end=planned_end,
        required_capacity_kg=row["cargo_weight_kg"]), actor=actor, now=now)
    after = _save_trip(conn, trip_code, status="ASSIGNED", booking_id=new["id"], actor=actor, now=now)
    write_audit(conn, actor=actor, action="TRIP_ASSIGN", object_type="TransportTrip",
                object_code=trip_code, at=now, before={"status": row["status"]},
                after={"status": "ASSIGNED", "booking_code": new["code"]})
    return trip_to_dict(conn, after, now)


def _require_assigned(row) -> None:
    if row["status"] != "ASSIGNED":
        raise Conflict(
            "Chỉ đổi hoặc gỡ phân công khi chuyến đang ASSIGNED (trước khi nhận hàng).",
            code="INVALID_STATE",
            details={"status": row["status"]},
        )


def change_trip_assignment(
    conn: sqlite3.Connection, trip_code: str, *, expected_version: int, vehicle_id: int,
    driver_id: int, planned_start: datetime, planned_end: datetime, reason: str, actor: str,
    now: datetime,
) -> dict:
    """Đổi xe/tài xế/khung giờ trước PICKED_UP. Thất bại thì giữ phân công cũ."""
    require_tx(conn)
    reason = clean_text(reason, "reason", max_len=NOTE_MAX)
    row = trip_row(conn, trip_code)
    check_version(row["version"], expected_version)
    _require_assigned(row)
    old = booking.get_booking(conn, row["booking_id"], now)
    new = booking.replace(conn, row["booking_id"], BookingRequest(
        source_type=TRANSPORT, source_code=trip_code, vehicle_id=vehicle_id, driver_id=driver_id,
        planned_start=planned_start, planned_end=planned_end,
        required_capacity_kg=row["cargo_weight_kg"]), reason=reason, actor=actor, now=now)
    after = _save_trip(conn, trip_code, status="ASSIGNED", booking_id=new["id"], actor=actor, now=now)
    write_audit(conn, actor=actor, action="TRIP_REASSIGN", object_type="TransportTrip",
                object_code=trip_code, at=now, before={"booking_code": old["code"]},
                after={"booking_code": new["code"]}, reason=reason)
    return trip_to_dict(conn, after, now)


def unassign_trip(
    conn: sqlite3.Connection, trip_code: str, *, expected_version: int, reason: str, actor: str,
    now: datetime,
) -> dict:
    """ASSIGNED → UNASSIGNED, giải phóng lịch giữ."""
    require_tx(conn)
    reason = clean_text(reason, "reason", max_len=NOTE_MAX)
    row = trip_row(conn, trip_code)
    check_version(row["version"], expected_version)
    _require_assigned(row)
    released = booking.release(conn, row["booking_id"], reason=reason, actor=actor, now=now)
    after = _save_trip(conn, trip_code, status="UNASSIGNED", booking_id=None, actor=actor, now=now)
    write_audit(conn, actor=actor, action="TRIP_UNASSIGN", object_type="TransportTrip",
                object_code=trip_code, at=now, before={"booking_code": released["code"]},
                after={"status": "UNASSIGNED"}, reason=reason)
    return trip_to_dict(conn, after, now)


def change_trip_planned_end(
    conn: sqlite3.Connection, trip_code: str, *, new_planned_end: datetime, reason: str, actor: str,
    now: datetime, expected_version: int | None = None,
) -> dict:
    """Cập nhật giờ kết thúc dự kiến (TRN-05). Chồng lịch sau thì báo, không ghi đè."""
    require_tx(conn)
    row = trip_row(conn, trip_code)
    if expected_version is not None:
        check_version(row["version"], expected_version)
    if row["status"] not in ("ASSIGNED", "PICKED_UP", "IN_TRANSIT"):
        raise Conflict("Chuyến không còn lịch giữ để đổi giờ.", code="INVALID_STATE",
                       details={"status": row["status"]})
    booking.change_planned_end(conn, row["booking_id"], new_planned_end, reason=reason,
                               actor=actor, now=now)
    conn.execute("UPDATE transport_trips SET version = version + 1, updated_at = ?, updated_by = ? WHERE code = ?",
                 (to_db(now), actor, trip_code))
    return get_trip(conn, trip_code, now)


# ---------- Hồ sơ thuê xe ----------

def rental_row(conn, code: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM rental_records WHERE code = ?", (code,)).fetchone()
    if row is None:
        raise NotFound("Không tìm thấy hồ sơ thuê xe.")
    return row


def rental_to_dict(conn, row: sqlite3.Row, now: datetime) -> dict:
    return {
        "code": row["code"],
        "order_code": row["order_code"],
        "vehicle_type": row["vehicle_type"],
        "min_capacity_kg": row["min_capacity_kg"],
        "requested_start": row["requested_start"],
        "requested_end": row["requested_end"],
        "status": row["status"],
        "version": row["version"],
        "booking": None if row["booking_id"] is None else booking.get_booking(conn, row["booking_id"], now),
    }


def get_rental(conn, code: str, now: datetime) -> dict:
    return rental_to_dict(conn, rental_row(conn, code), now)


def _save_rental(conn, code: str, *, status: str, booking_id: int | None, actor: str, now: datetime):
    conn.execute(
        "UPDATE rental_records SET status = ?, booking_id = ?, version = version + 1, "
        "updated_at = ?, updated_by = ? WHERE code = ?",
        (status, booking_id, to_db(now), actor, code),
    )
    return rental_row(conn, code)


def create_rental_record(
    conn: sqlite3.Connection, *, order_code: str, vehicle_type: str, min_capacity_kg: int,
    requested_start: datetime, requested_end: datetime, actor: str, now: datetime,
) -> dict:
    """Một đơn thuê có đúng một hồ sơ thuê; gọi lại trả về hồ sơ đã có."""
    require_tx(conn)
    order_code = clean_text(order_code, "order_code", max_len=50)
    existing = conn.execute("SELECT * FROM rental_records WHERE order_code = ?", (order_code,)).fetchone()
    if existing is not None:
        return rental_to_dict(conn, existing, now)
    vehicle_type = clean_text(vehicle_type, "vehicle_type", max_len=50)
    if min_capacity_kg is None or min_capacity_kg <= 0:
        raise ValidationFailed("Tải trọng mong muốn phải lớn hơn 0.", details={"field": "min_capacity_kg"})
    start_u, end_u = to_utc(requested_start), to_utc(requested_end)
    if end_u <= start_u:
        raise ValidationFailed("Thời điểm kết thúc phải sau thời điểm bắt đầu.",
                               details={"field": "requested_end"})
    code = next_code(conn, "RNT")
    now_s = to_db(now)
    conn.execute(
        "INSERT INTO rental_records(code, order_code, vehicle_type, min_capacity_kg, requested_start, "
        "requested_end, status, created_at, created_by, updated_at, updated_by) "
        "VALUES (?, ?, ?, ?, ?, ?, 'UNASSIGNED', ?, ?, ?, ?)",
        (code, order_code, vehicle_type, min_capacity_kg, to_db(start_u), to_db(end_u),
         now_s, actor, now_s, actor),
    )
    write_audit(conn, actor=actor, action="RENTAL_CREATE", object_type="RentalRecord", object_code=code,
                at=now, after={"order_code": order_code, "vehicle_type": vehicle_type})
    return get_rental(conn, code, now)


def _rental_request(row, vehicle_id: int, start: datetime | None, end: datetime | None) -> BookingRequest:
    return BookingRequest(
        source_type=RENTAL, source_code=row["code"], vehicle_id=vehicle_id, driver_id=None,
        planned_start=start or from_db(row["requested_start"]),
        planned_end=end or from_db(row["requested_end"]),
        required_capacity_kg=row["min_capacity_kg"], required_vehicle_type=row["vehicle_type"],
    )


def reserve_rental(
    conn: sqlite3.Connection, rental_code: str, *, vehicle_id: int, actor: str, now: datetime,
    planned_start: datetime | None = None, planned_end: datetime | None = None,
) -> dict:
    """UNASSIGNED → RESERVED. Mặc định giữ đúng khung giờ khách đặt (RNT-01)."""
    require_tx(conn)
    row = rental_row(conn, rental_code)
    if row["status"] != "UNASSIGNED":
        raise Conflict(
            f"Hồ sơ thuê đang ở trạng thái {row['status']}; chỉ giữ xe được cho hồ sơ UNASSIGNED. "
            "Tải lại để xem xe đã giữ.",
            code="INVALID_STATE",
            details={"status": row["status"]},
        )
    new = booking.hold(conn, _rental_request(row, vehicle_id, planned_start, planned_end),
                       actor=actor, now=now)
    after = _save_rental(conn, rental_code, status="RESERVED", booking_id=new["id"], actor=actor, now=now)
    write_audit(conn, actor=actor, action="RENTAL_RESERVE", object_type="RentalRecord",
                object_code=rental_code, at=now, before={"status": row["status"]},
                after={"status": "RESERVED", "booking_code": new["code"]})
    return rental_to_dict(conn, after, now)


def _require_reserved(row) -> None:
    if row["status"] != "RESERVED":
        raise Conflict(
            "Chỉ đổi hoặc bỏ giữ xe khi hồ sơ đang RESERVED (trước khi bàn giao).",
            code="INVALID_STATE",
            details={"status": row["status"]},
        )


def change_rental_reservation(
    conn: sqlite3.Connection, rental_code: str, *, expected_version: int, vehicle_id: int,
    planned_start: datetime, planned_end: datetime, reason: str, customer_note: str, actor: str,
    now: datetime,
) -> dict:
    """Đổi xe hoặc khung giờ đã thống nhất với khách (RNT-02)."""
    require_tx(conn)
    reason = clean_text(reason, "reason", max_len=NOTE_MAX)
    customer_note = clean_text(customer_note, "customer_note", max_len=NOTE_MAX)
    row = rental_row(conn, rental_code)
    check_version(row["version"], expected_version)
    _require_reserved(row)
    old = booking.get_booking(conn, row["booking_id"], now)
    new = booking.replace(conn, row["booking_id"], _rental_request(row, vehicle_id, planned_start, planned_end),
                          reason=reason, actor=actor, now=now)
    after = _save_rental(conn, rental_code, status="RESERVED", booking_id=new["id"], actor=actor, now=now)
    write_audit(conn, actor=actor, action="RENTAL_CHANGE", object_type="RentalRecord",
                object_code=rental_code, at=now, before={"booking_code": old["code"]},
                after={"booking_code": new["code"], "customer_note": customer_note}, reason=reason)
    return rental_to_dict(conn, after, now)


def release_rental_reservation(
    conn: sqlite3.Connection, rental_code: str, *, expected_version: int, reason: str,
    customer_note: str, actor: str, now: datetime,
) -> dict:
    """RESERVED → UNASSIGNED. Không hủy đơn; hủy đơn đi theo SAL-09."""
    require_tx(conn)
    reason = clean_text(reason, "reason", max_len=NOTE_MAX)
    customer_note = clean_text(customer_note, "customer_note", max_len=NOTE_MAX)
    row = rental_row(conn, rental_code)
    check_version(row["version"], expected_version)
    _require_reserved(row)
    released = booking.release(conn, row["booking_id"], reason=reason, actor=actor, now=now)
    after = _save_rental(conn, rental_code, status="UNASSIGNED", booking_id=None, actor=actor, now=now)
    write_audit(conn, actor=actor, action="RENTAL_RELEASE", object_type="RentalRecord",
                object_code=rental_code, at=now, before={"booking_code": released["code"]},
                after={"status": "UNASSIGNED", "customer_note": customer_note}, reason=reason)
    return rental_to_dict(conn, after, now)


# ---------- Hủy đơn (điểm nối cho SAL-09 của BE1) ----------

def cancel_operational_record(
    conn: sqlite3.Connection, *, source_type: str, source_code: str, reason: str, actor: str,
    now: datetime,
) -> dict:
    """Đưa chuyến/hồ sơ thuê/lô kho về CANCELLED, giải phóng lịch giữ, trong cùng transaction.

    source_type: TRANSPORT (mã chuyến), RENTAL (mã hồ sơ thuê) hoặc WAREHOUSE (mã lô).
    Chỉ cho khi chưa bắt đầu thực hiện (BR-15). BE1 kiểm tra phần còn lại của BR-15
    (khoản thu, phụ phí) trước khi gọi hàm này.
    """
    require_tx(conn)
    reason = clean_text(reason, "reason", max_len=NOTE_MAX)
    if source_type == WAREHOUSE:
        return warehouse.cancel_lot(conn, source_code, reason=reason, actor=actor, now=now)
    if source_type == TRANSPORT:
        row, allowed, table = trip_row(conn, source_code), ("UNASSIGNED", "ASSIGNED"), "transport_trips"
    elif source_type == RENTAL:
        row, allowed, table = rental_row(conn, source_code), ("UNASSIGNED", "RESERVED"), "rental_records"
    else:
        raise ValueError(f"source_type không hợp lệ: {source_type}")
    if row["status"] not in allowed:
        raise Conflict(
            f"Bản ghi đang {row['status']}, đã bắt đầu thực hiện nên không hủy được theo quy trình chuẩn.",
            code="NOT_CANCELLABLE",
            details={"status": row["status"]},
        )
    booking.release_for_source(conn, source_type, source_code, reason=reason, actor=actor, now=now)
    conn.execute(
        f"UPDATE {table} SET status = 'CANCELLED', version = version + 1, updated_at = ?, "
        "updated_by = ? WHERE code = ?",
        (to_db(now), actor, source_code),
    )
    write_audit(conn, actor=actor, action="OPERATION_CANCEL", object_type=table, object_code=source_code,
                at=now, before={"status": row["status"]}, after={"status": "CANCELLED"}, reason=reason)
    return get_trip(conn, source_code, now) if source_type == TRANSPORT else get_rental(conn, source_code, now)
