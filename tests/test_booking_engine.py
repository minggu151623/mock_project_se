"""Engine giữ lịch: BR-07/08/09 và các tình huống NEG-06, 07, 08, 10."""

import pytest

from backend import booking
from backend.booking import RENTAL, TRANSPORT, BookingRequest
from backend.db import write_tx
from backend.errors import BookingConflict, Conflict, ValidationFailed
from tests import factories as f


def req(source, vehicle, driver=None, start=None, end=None, kg=1000.0, source_type=TRANSPORT, vtype=None):
    return BookingRequest(
        source_type=source_type, source_code=source, vehicle_id=vehicle["id"],
        driver_id=driver["id"] if driver else None, planned_start=start, planned_end=end,
        required_capacity_kg=kg, required_vehicle_type=vtype)


def hold(conn, clock, r):
    with write_tx(conn):
        return booking.hold(conn, r, actor="dsp1", now=clock())


def active_count(conn):
    return conn.execute("SELECT COUNT(*) FROM resource_bookings WHERE status <> 'RELEASED'").fetchone()[0]


@pytest.fixture
def v1(conn, clock):
    return f.vehicle(conn, clock(), plate="51C-100.01", capacity=2000)


@pytest.fixture
def v2(conn, clock):
    return f.vehicle(conn, clock(), plate="51C-100.02", capacity=2000)


@pytest.fixture
def d1(conn, clock):
    return f.driver(conn, clock(), name="Tài xế 1")


@pytest.fixture
def d2(conn, clock):
    return f.driver(conn, clock(), name="Tài xế 2", phone="0907654321")


def test_adjacent_windows_do_not_conflict(conn, clock, v1, d1):
    a = hold(conn, clock, req("TRP-A", v1, d1, f.vn(8), f.vn(10)))
    b = hold(conn, clock, req("TRP-B", v1, d1, f.vn(10), f.vn(12)))
    assert a["status"] == b["status"] == "HELD"
    assert b["planned_start"] == "2026-12-01T03:00:00Z"


def test_vehicle_overlap_rejected_without_partial_booking(conn, clock, v1, d1, d2):
    a = hold(conn, clock, req("TRP-A", v1, d1, f.vn(8), f.vn(10)))
    with pytest.raises(BookingConflict) as exc:
        hold(conn, clock, req("TRP-B", v1, d2, f.vn(9), f.vn(11)))
    conflicts = exc.value.details["conflicts"]
    assert [(c["resource"], c["booking_code"]) for c in conflicts] == [("VEHICLE", a["code"])]
    assert active_count(conn) == 1


def test_transport_and_rental_share_one_vehicle_schedule(conn, clock, v1, d1):
    hold(conn, clock, req("RNT-X", v1, None, f.vn(8), f.vn(12), source_type=RENTAL))
    with pytest.raises(BookingConflict) as exc:
        hold(conn, clock, req("TRP-A", v1, d1, f.vn(11), f.vn(13)))
    assert exc.value.details["conflicts"][0]["source_type"] == RENTAL


def test_driver_overlap_rejected_across_vehicles(conn, clock, v1, v2, d1):
    hold(conn, clock, req("TRP-A", v1, d1, f.vn(8), f.vn(10)))
    with pytest.raises(BookingConflict) as exc:
        hold(conn, clock, req("TRP-B", v2, d1, f.vn(9), f.vn(11)))
    assert exc.value.details["conflicts"][0]["resource"] == "DRIVER"


@pytest.mark.parametrize("case, code", [
    ("maintenance", "VEHICLE_NOT_ELIGIBLE"),
    ("inactive_vehicle", "VEHICLE_NOT_ELIGIBLE"),
    ("inactive_driver", "DRIVER_NOT_ELIGIBLE"),
    ("under_capacity", "CAPACITY_INSUFFICIENT"),
])
def test_ineligible_resources_rejected(conn, clock, case, code):
    now = clock()
    vehicle = f.vehicle(conn, now, plate="51C-200.01", capacity=1000 if case == "under_capacity" else 2000,
                        status={"maintenance": "MAINTENANCE", "inactive_vehicle": "INACTIVE"}.get(case, "ACTIVE"))
    driver = f.driver(conn, now, status="INACTIVE" if case == "inactive_driver" else "ACTIVE")
    with pytest.raises(Conflict) as exc:
        hold(conn, clock, req("TRP-A", vehicle, driver, f.vn(8), f.vn(10), kg=1200))
    assert exc.value.code == code
    assert active_count(conn) == 0


def test_rental_type_mismatch_rejected(conn, clock, v1):
    with pytest.raises(Conflict) as exc:
        hold(conn, clock, req("RNT-X", v1, None, f.vn(8), f.vn(10), source_type=RENTAL, vtype="VAN"))
    assert exc.value.code == "VEHICLE_TYPE_MISMATCH"


@pytest.mark.parametrize("start, end, field", [
    (f.vn(10), f.vn(10), "planned_end"),
    (f.vn(10), f.vn(9), "planned_end"),
    (f.vn(6, 50), f.vn(9), "planned_start"),  # bây giờ là 07:00
])
def test_window_validation(conn, clock, v1, d1, start, end, field):
    with pytest.raises(ValidationFailed) as exc:
        hold(conn, clock, req("TRP-A", v1, d1, start, end))
    assert exc.value.details["field"] == field


def test_planned_start_allows_one_minute_grace(conn, clock, v1, d1):
    assert hold(conn, clock, req("TRP-A", v1, d1, f.vn(6, 59, ), f.vn(9)))["status"] == "HELD"


def test_driver_and_source_rules(conn, clock, v1, d1):
    with pytest.raises(ValidationFailed):
        hold(conn, clock, req("TRP-A", v1, None, f.vn(8), f.vn(10)))
    with pytest.raises(ValidationFailed):
        hold(conn, clock, req("RNT-X", v1, d1, f.vn(8), f.vn(10), source_type=RENTAL))
    hold(conn, clock, req("TRP-A", v1, d1, f.vn(8), f.vn(10)))
    with pytest.raises(Conflict) as exc:
        hold(conn, clock, req("TRP-A", v1, d1, f.vn(13), f.vn(14)))
    assert exc.value.code == "SOURCE_ALREADY_BOOKED"


def test_next_job_blocked_until_vehicle_actually_returned(conn, clock, v1, d1):
    """NEG-06/BR-09: đã qua giờ hẹn trả nhưng xe chưa về thì chưa bắt đầu việc sau."""
    a = hold(conn, clock, req("TRP-A", v1, d1, f.vn(8), f.vn(10)))
    b = hold(conn, clock, req("TRP-B", v1, d1, f.vn(10), f.vn(12)))
    clock.set(f.vn(8, 5))
    with write_tx(conn):
        booking.start_usage(conn, a["id"], actual_start=f.vn(8), actor="dsp1", now=clock())

    clock.set(f.vn(10, 30))
    with pytest.raises(Conflict) as exc, write_tx(conn):
        booking.start_usage(conn, b["id"], actual_start=f.vn(10, 30), actor="dsp1", now=clock())
    assert exc.value.code == "RESOURCE_STILL_IN_USE"
    assert exc.value.details["blocking"][0]["booking_code"] == a["code"]
    assert booking.get_booking(conn, a["id"], clock())["overdue"] is True

    clock.set(f.vn(10, 45))
    with write_tx(conn):
        released = booking.release(conn, a["id"], reason="Xe về bãi", actual_end=f.vn(10, 40),
                                   actor="dsp1", now=clock())
        started = booking.start_usage(conn, b["id"], actual_start=f.vn(10, 45), actor="dsp1", now=clock())
    assert released["actual_end"] == "2026-12-01T03:40:00Z"
    assert started["status"] == "IN_USE"


def test_extension_that_overlaps_next_booking_keeps_current(conn, clock, v1, d1):
    """NEG-10: kéo dài chồng lịch sau thì báo xung đột, không ghi đè."""
    a = hold(conn, clock, req("TRP-A", v1, d1, f.vn(8), f.vn(10)))
    b = hold(conn, clock, req("TRP-B", v1, d1, f.vn(10), f.vn(12)))
    with pytest.raises(BookingConflict) as exc, write_tx(conn):
        booking.change_planned_end(conn, a["id"], f.vn(11), reason="Kẹt xe", actor="dsp1", now=clock())
    assert {c["booking_code"] for c in exc.value.details["conflicts"]} == {b["code"]}
    assert booking.get_booking(conn, a["id"], clock())["planned_end"] == a["planned_end"]

    with write_tx(conn):
        booking.release(conn, b["id"], reason="Dời sang ngày khác", actor="dsp1", now=clock())
        extended = booking.change_planned_end(conn, a["id"], f.vn(11), reason="Kẹt xe", actor="dsp1",
                                              now=clock())
    assert extended["planned_end"] == "2026-12-01T04:00:00Z"


def test_failed_replace_keeps_old_booking(conn, clock, v1, v2, d1, d2):
    """NEG-08: đổi sang xe đã kín lịch thì thất bại và giữ nguyên booking cũ."""
    a = hold(conn, clock, req("TRP-A", v1, d1, f.vn(8), f.vn(10)))
    hold(conn, clock, req("TRP-C", v2, d2, f.vn(8), f.vn(10)))
    with pytest.raises(BookingConflict), write_tx(conn):
        booking.replace(conn, a["id"], req("TRP-A", v2, d1, f.vn(8), f.vn(10)), reason="Đổi xe",
                        actor="dsp1", now=clock())
    still = booking.get_booking(conn, a["id"], clock())
    assert still["status"] == "HELD" and still["vehicle"]["id"] == v1["id"]
    assert active_count(conn) == 2


def test_replace_inside_same_window_succeeds(conn, clock, v1, d1, d2):
    a = hold(conn, clock, req("TRP-A", v1, d1, f.vn(8), f.vn(10)))
    with write_tx(conn):
        new = booking.replace(conn, a["id"], req("TRP-A", v1, d2, f.vn(8), f.vn(10)), reason="Đổi tài xế",
                              actor="dsp1", now=clock())
    assert new["driver"]["id"] == d2["id"]
    assert booking.get_booking(conn, a["id"], clock())["status"] == "RELEASED"


def test_failed_replace_inside_caller_transaction_still_restores(conn, clock, v1, v2, d1, d2):
    """SAVEPOINT: người gọi bắt lỗi rồi commit vẫn không mất booking cũ."""
    a = hold(conn, clock, req("TRP-A", v1, d1, f.vn(8), f.vn(10)))
    hold(conn, clock, req("TRP-C", v2, d2, f.vn(8), f.vn(10)))
    with write_tx(conn):
        with pytest.raises(BookingConflict):
            booking.replace(conn, a["id"], req("TRP-A", v2, d1, f.vn(8), f.vn(10)), reason="Đổi xe",
                            actor="dsp1", now=clock())
    assert booking.get_booking(conn, a["id"], clock())["status"] == "HELD"


def test_start_outside_booked_window_rejected(conn, clock, v1, d1):
    a = hold(conn, clock, req("TRP-A", v1, d1, f.vn(8), f.vn(10)))
    clock.set(f.vn(8))
    with pytest.raises(Conflict) as exc, write_tx(conn):
        booking.start_usage(conn, a["id"], actual_start=f.vn(7, 50), actor="dsp1", now=clock())
    assert exc.value.code == "OUTSIDE_BOOKED_WINDOW"


def test_release_in_use_validates_actual_end(conn, clock, v1, d1):
    a = hold(conn, clock, req("TRP-A", v1, d1, f.vn(8), f.vn(10)))
    clock.set(f.vn(9))
    with write_tx(conn):
        booking.start_usage(conn, a["id"], actual_start=f.vn(8, 30), actor="dsp1", now=clock())
    for bad in (None, f.vn(9, 30), f.vn(8, 0)):
        with pytest.raises(ValidationFailed) as exc, write_tx(conn):
            booking.release(conn, a["id"], reason="Trả xe", actual_end=bad, actor="dsp1", now=clock())
        assert exc.value.details["field"] == "actual_end"


def test_release_for_source_refuses_started_booking(conn, clock, v1, d1):
    a = hold(conn, clock, req("TRP-A", v1, d1, f.vn(8), f.vn(10)))
    clock.set(f.vn(8, 10))
    with write_tx(conn):
        booking.start_usage(conn, a["id"], actual_start=f.vn(8, 5), actor="dsp1", now=clock())
    with pytest.raises(Conflict) as exc, write_tx(conn):
        booking.release_for_source(conn, TRANSPORT, "TRP-A", reason="Khách hủy", actor="dsp1", now=clock())
    assert exc.value.code == "RESOURCE_IN_USE"


def test_hold_and_release_are_audited(conn, clock, v1, d1):
    a = hold(conn, clock, req("TRP-A", v1, d1, f.vn(8), f.vn(10)))
    with write_tx(conn):
        booking.release(conn, a["id"], reason="Khách đổi lịch", actor="dsp1", now=clock())
    rows = conn.execute(
        "SELECT action, actor, reason FROM audit_log WHERE object_code = ? ORDER BY id", (a["code"],)
    ).fetchall()
    assert [(r["action"], r["actor"], r["reason"]) for r in rows] == [
        ("BOOKING_HOLD", "dsp1", None), ("BOOKING_RELEASE", "dsp1", "Khách đổi lịch")]


def test_write_functions_require_transaction(conn, clock, v1, d1):
    with pytest.raises(RuntimeError):
        booking.hold(conn, req("TRP-A", v1, d1, f.vn(8), f.vn(10)), actor="dsp1", now=clock())
