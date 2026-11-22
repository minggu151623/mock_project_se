"""NFR-05, NEG-07: hai điều phối thao tác cùng lúc chỉ một người thành công."""

import threading

from backend import assignments, booking
from backend.booking import TRANSPORT, BookingRequest
from backend.db import connect, write_tx
from backend.errors import DomainError
from tests import factories as f


def race(db_path, *actions):
    """Chạy các thao tác trên kết nối riêng, xuất phát cùng lúc."""
    barrier = threading.Barrier(len(actions))
    results = [None] * len(actions)

    def run(i, action):
        c = connect(db_path)
        try:
            barrier.wait()
            with write_tx(c):
                results[i] = action(c)
        except DomainError as exc:
            results[i] = exc
        finally:
            c.close()

    threads = [threading.Thread(target=run, args=(i, a)) for i, a in enumerate(actions)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def test_two_dispatchers_hold_same_vehicle(db_path, conn, clock):
    vehicle = f.vehicle(conn, clock())
    drivers = [f.driver(conn, clock(), name=f"TX {i}", phone=f"09000000{i:02d}") for i in range(2)]
    for day in range(2, 12):  # lặp nhiều vòng để bắt lỗi chạy đua
        def hold(i):
            return lambda c: booking.hold(c, BookingRequest(
                source_type=TRANSPORT, source_code=f"TRP-{day}-{i}", vehicle_id=vehicle["id"],
                driver_id=drivers[i]["id"], planned_start=f.vn(8, day=day), planned_end=f.vn(10, day=day),
                required_capacity_kg=500), actor=f"dsp{i}", now=clock())

        results = race(db_path, hold(0), hold(1))
        ok = [r for r in results if isinstance(r, dict)]
        failed = [r for r in results if isinstance(r, DomainError)]
        assert len(ok) == 1 and len(failed) == 1, results
        assert failed[0].code == "BOOKING_CONFLICT"
    total = conn.execute("SELECT COUNT(*) FROM resource_bookings").fetchone()[0]
    assert total == 10


def test_two_dispatchers_assign_same_trip(db_path, conn, clock):
    now = clock()
    v = [f.vehicle(conn, now, plate=f"51C-400.0{i}") for i in range(2)]
    d = [f.driver(conn, now, name=f"TX {i}", phone=f"09110000{i:02d}") for i in range(2)]
    trip = f.trip(conn, now)

    def assign(i):
        return lambda c: assignments.assign_trip(
            c, trip["code"], vehicle_id=v[i]["id"], driver_id=d[i]["id"], planned_start=f.vn(8),
            planned_end=f.vn(10), actor=f"dsp{i}", now=now)

    results = race(db_path, assign(0), assign(1))
    failed = [r for r in results if isinstance(r, DomainError)]
    assert len(failed) == 1 and failed[0].code == "INVALID_STATE"
    assert conn.execute("SELECT COUNT(*) FROM resource_bookings").fetchone()[0] == 1
