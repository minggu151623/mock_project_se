"""Lưới an toàn trong DB: ghi thẳng bằng SQL, bỏ qua engine, vẫn bị chặn."""

import sqlite3

import pytest

from tests import factories as f

NOW = "2026-12-01T00:00:00Z"


def raw_insert(conn, code, source, vehicle_id, driver_id, start, end, *, status="HELD",
               source_type="TRANSPORT", actual_start=None):
    conn.execute(
        "INSERT INTO resource_bookings(code, source_type, source_code, vehicle_id, driver_id, planned_start, "
        "planned_end, status, actual_start, created_at, created_by, updated_at, updated_by) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'script', ?, 'script')",
        (code, source_type, source, vehicle_id, driver_id, start, end, status, actual_start, NOW, NOW),
    )


@pytest.fixture
def res(conn, clock):
    now = clock()
    return {
        "v1": f.vehicle(conn, now, plate="51C-300.01")["id"],
        "v2": f.vehicle(conn, now, plate="51C-300.02")["id"],
        "d1": f.driver(conn, now, name="A")["id"],
        "d2": f.driver(conn, now, name="B", phone="0907654321")["id"],
    }


def test_trigger_blocks_vehicle_overlap(conn, res):
    raw_insert(conn, "BK-X1", "TRP-1", res["v1"], res["d1"], "2026-12-01T01:00:00Z", "2026-12-01T03:00:00Z")
    with pytest.raises(sqlite3.IntegrityError, match="BOOKING_OVERLAP_VEHICLE"):
        raw_insert(conn, "BK-X2", "TRP-2", res["v1"], res["d2"], "2026-12-01T02:00:00Z", "2026-12-01T04:00:00Z")


def test_trigger_blocks_driver_overlap(conn, res):
    raw_insert(conn, "BK-X1", "TRP-1", res["v1"], res["d1"], "2026-12-01T01:00:00Z", "2026-12-01T03:00:00Z")
    with pytest.raises(sqlite3.IntegrityError, match="BOOKING_OVERLAP_DRIVER"):
        raw_insert(conn, "BK-X2", "TRP-2", res["v2"], res["d1"], "2026-12-01T02:00:00Z", "2026-12-01T04:00:00Z")


def test_trigger_allows_adjacent_and_released(conn, res):
    raw_insert(conn, "BK-X1", "TRP-1", res["v1"], res["d1"], "2026-12-01T01:00:00Z", "2026-12-01T03:00:00Z")
    raw_insert(conn, "BK-X2", "TRP-2", res["v1"], res["d1"], "2026-12-01T03:00:00Z", "2026-12-01T05:00:00Z")
    raw_insert(conn, "BK-X3", "TRP-3", res["v1"], res["d1"], "2026-12-01T01:00:00Z", "2026-12-01T05:00:00Z",
               status="RELEASED")


def test_trigger_blocks_update_into_overlap(conn, res):
    raw_insert(conn, "BK-X1", "TRP-1", res["v1"], res["d1"], "2026-12-01T01:00:00Z", "2026-12-01T03:00:00Z")
    raw_insert(conn, "BK-X2", "TRP-2", res["v1"], res["d2"], "2026-12-01T03:00:00Z", "2026-12-01T05:00:00Z")
    with pytest.raises(sqlite3.IntegrityError, match="BOOKING_OVERLAP_VEHICLE"):
        conn.execute("UPDATE resource_bookings SET planned_end = '2026-12-01T04:00:00Z' WHERE code = 'BK-X1'")


def test_one_vehicle_in_use_at_a_time(conn, res):
    raw_insert(conn, "BK-X1", "TRP-1", res["v1"], res["d1"], "2026-12-01T01:00:00Z", "2026-12-01T03:00:00Z",
               status="IN_USE", actual_start="2026-12-01T01:00:00Z")
    raw_insert(conn, "BK-X2", "TRP-2", res["v1"], res["d2"], "2026-12-01T03:00:00Z", "2026-12-01T05:00:00Z")
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        conn.execute("UPDATE resource_bookings SET status = 'IN_USE', actual_start = '2026-12-01T03:30:00Z' "
                     "WHERE code = 'BK-X2'")


def test_one_active_booking_per_source(conn, res):
    raw_insert(conn, "BK-X1", "TRP-1", res["v1"], res["d1"], "2026-12-01T01:00:00Z", "2026-12-01T03:00:00Z")
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        raw_insert(conn, "BK-X2", "TRP-1", res["v2"], res["d2"], "2026-12-01T05:00:00Z", "2026-12-01T06:00:00Z")


@pytest.mark.parametrize("start", ["2026-12-01 01:00:00", "2026-12-01T08:00:00+07:00"])
def test_time_format_enforced(conn, res, start):
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        raw_insert(conn, "BK-X1", "TRP-1", res["v1"], res["d1"], start, "2026-12-01T03:00:00Z")


def test_rental_with_driver_rejected(conn, res):
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        raw_insert(conn, "BK-X1", "RNT-1", res["v1"], res["d1"], "2026-12-01T01:00:00Z",
                   "2026-12-01T03:00:00Z", source_type="RENTAL")


def test_released_booking_cannot_reopen(conn, res):
    raw_insert(conn, "BK-X1", "TRP-1", res["v1"], res["d1"], "2026-12-01T01:00:00Z", "2026-12-01T03:00:00Z",
               status="RELEASED")
    with pytest.raises(sqlite3.IntegrityError, match="BOOKING_RELEASED_IMMUTABLE"):
        conn.execute("UPDATE resource_bookings SET status = 'HELD' WHERE code = 'BK-X1'")


def test_audit_log_is_append_only(conn, res):
    with pytest.raises(sqlite3.IntegrityError, match="AUDIT_APPEND_ONLY"):
        conn.execute("UPDATE audit_log SET actor = 'x'")
    with pytest.raises(sqlite3.IntegrityError, match="AUDIT_APPEND_ONLY"):
        conn.execute("DELETE FROM audit_log")
