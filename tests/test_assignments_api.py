"""TRN-02, TRN-05, RNT-01, RNT-02 qua API và điểm nối hủy đơn SAL-09."""

import pytest

from backend import assignments, booking
from backend.booking import RENTAL, TRANSPORT
from backend.db import write_tx
from backend.errors import Conflict
from tests import factories as f
from tests.factories import DSP, DSP2


@pytest.fixture
def setup(conn, clock):
    now = clock()
    return {
        "v1": f.vehicle(conn, now, plate="51C-600.01", capacity=2000),
        "v2": f.vehicle(conn, now, plate="51C-600.02", capacity=2000),
        "van": f.vehicle(conn, now, plate="51C-600.03", capacity=1500, vtype="VAN"),
        "d1": f.driver(conn, now, name="Tài xế 1"),
        "d2": f.driver(conn, now, name="Tài xế 2", phone="0907654321"),
        "trip": f.trip(conn, now, order="ORD-T-001", kg=1200),
        "trip2": f.trip(conn, now, order="ORD-T-002", kg=800),
        "rental": f.rental(conn, now, order="ORD-R-001", vtype="TRUCK_2T", min_kg=1000),
    }


def body(vehicle, driver, start=8, end=10):
    return {"vehicle_id": vehicle["id"], "driver_id": driver["id"],
            "planned_start": f.iso(start), "planned_end": f.iso(end)}


def trip_of(client, code):
    return client.get(f"/api/trips/{code}", headers=DSP).json()


def test_assign_trip_creates_booking_on_shared_schedule(client, setup):
    s = setup
    r = client.post(f"/api/trips/{s['trip']['code']}/assignment", headers=DSP, json=body(s["v1"], s["d1"]))
    assert r.status_code == 201
    trip = r.json()
    assert trip["status"] == "ASSIGNED"
    assert trip["booking"]["vehicle"]["id"] == s["v1"]["id"]
    assert trip["booking"]["planned_start"] == "2026-12-01T01:00:00Z"
    assert "X-Request-ID" in r.headers

    sched = client.get("/api/schedule", headers=DSP,
                       params={"start": f.iso(0), "end": f.iso(23, 59), "vehicle_id": s["v1"]["id"]}).json()
    assert sched["total"] == 1
    assert sched["items"][0]["source_type"] == TRANSPORT
    assert sched["items"][0]["source_code"] == s["trip"]["code"]


def test_resubmitting_with_same_key_returns_same_result(client, setup, conn):
    s = setup
    url = f"/api/trips/{s['trip']['code']}/assignment"
    headers = {**DSP, "Idempotency-Key": "assign-001"}
    first = client.post(url, headers=headers, json=body(s["v1"], s["d1"]))
    again = client.post(url, headers=headers, json=body(s["v1"], s["d1"]))
    assert first.status_code == again.status_code == 201
    assert first.json()["booking"]["code"] == again.json()["booking"]["code"]
    assert conn.execute("SELECT COUNT(*) FROM resource_bookings").fetchone()[0] == 1

    reused = client.post(url, headers=headers, json=body(s["v2"], s["d2"]))
    assert reused.status_code == 409
    assert reused.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"


def test_second_assign_does_not_overwrite(client, setup):
    s = setup
    url = f"/api/trips/{s['trip']['code']}/assignment"
    client.post(url, headers=DSP, json=body(s["v1"], s["d1"]))
    r = client.post(url, headers=DSP2, json=body(s["v2"], s["d2"]))
    assert r.status_code == 409 and r.json()["error"]["code"] == "INVALID_STATE"
    assert trip_of(client, s["trip"]["code"])["booking"]["vehicle"]["id"] == s["v1"]["id"]


def test_failed_reassign_keeps_old_assignment(client, setup):
    """NEG-08."""
    s = setup
    client.post(f"/api/trips/{s['trip']['code']}/assignment", headers=DSP, json=body(s["v1"], s["d1"]))
    client.post(f"/api/trips/{s['trip2']['code']}/assignment", headers=DSP, json=body(s["v2"], s["d2"]))
    current = trip_of(client, s["trip"]["code"])

    r = client.put(f"/api/trips/{s['trip']['code']}/assignment", headers=DSP,
                   json={**body(s["v2"], s["d1"]), "version": current["version"], "reason": "Xe hỏng"})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "BOOKING_CONFLICT"
    assert r.json()["error"]["details"]["conflicts"][0]["source_code"] == s["trip2"]["code"]
    after = trip_of(client, s["trip"]["code"])
    assert after["booking"]["code"] == current["booking"]["code"]
    assert after["version"] == current["version"]


def test_reassign_moves_booking_and_frees_old_vehicle(client, setup):
    s = setup
    client.post(f"/api/trips/{s['trip']['code']}/assignment", headers=DSP, json=body(s["v1"], s["d1"]))
    current = trip_of(client, s["trip"]["code"])
    stale = client.put(f"/api/trips/{s['trip']['code']}/assignment", headers=DSP,
                       json={**body(s["v2"], s["d1"]), "version": current["version"] - 1, "reason": "Xe hỏng"})
    assert stale.json()["error"]["code"] == "VERSION_CONFLICT"

    r = client.put(f"/api/trips/{s['trip']['code']}/assignment", headers=DSP,
                   json={**body(s["v2"], s["d1"]), "version": current["version"], "reason": "Xe hỏng"})
    assert r.status_code == 200
    assert r.json()["booking"]["vehicle"]["id"] == s["v2"]["id"]
    free = client.get("/api/availability/vehicles", headers=DSP,
                      params={"start": f.iso(8), "end": f.iso(10), "min_capacity_kg": 1200}).json()["items"]
    assert s["v1"]["id"] in {v["id"] for v in free}


def test_unassign_returns_trip_to_unassigned(client, setup):
    s = setup
    client.post(f"/api/trips/{s['trip']['code']}/assignment", headers=DSP, json=body(s["v1"], s["d1"]))
    current = trip_of(client, s["trip"]["code"])
    missing_reason = client.post(f"/api/trips/{s['trip']['code']}/assignment/release", headers=DSP,
                                 json={"version": current["version"], "reason": "   "})
    assert missing_reason.status_code == 422
    r = client.post(f"/api/trips/{s['trip']['code']}/assignment/release", headers=DSP,
                    json={"version": current["version"], "reason": "Khách dời lịch"})
    assert r.status_code == 200
    assert r.json()["status"] == "UNASSIGNED" and r.json()["booking"] is None


def test_capacity_checked_against_trip_cargo(client, setup, conn, clock):
    s = setup
    r = client.post(f"/api/trips/{s['trip']['code']}/assignment", headers=DSP, json=body(s["van"], s["d1"]))
    assert r.status_code == 201  # VAN 1500 kg đủ cho 1200 kg
    heavy = f.trip(conn, clock(), order="ORD-T-003", kg=1800)
    r2 = client.post(f"/api/trips/{heavy['code']}/assignment", headers=DSP, json=body(s["van"], s["d2"], 12, 14))
    assert r2.status_code == 409 and r2.json()["error"]["code"] == "CAPACITY_INSUFFICIENT"


def test_extension_conflict_reported_and_not_applied(client, setup):
    """NEG-10 qua API."""
    s = setup
    client.post(f"/api/trips/{s['trip']['code']}/assignment", headers=DSP, json=body(s["v1"], s["d1"], 8, 10))
    client.post(f"/api/trips/{s['trip2']['code']}/assignment", headers=DSP, json=body(s["v1"], s["d2"], 10, 12))
    r = client.post(f"/api/trips/{s['trip']['code']}/planned-end", headers=DSP,
                    json={"new_planned_end": f.iso(11), "reason": "Kẹt xe trên QL1"})
    assert r.status_code == 409
    assert r.json()["error"]["details"]["conflicts"][0]["source_code"] == s["trip2"]["code"]
    assert trip_of(client, s["trip"]["code"])["booking"]["planned_end"] == "2026-12-01T03:00:00Z"


def test_rental_reserve_change_and_release(client, setup):
    s = setup
    code = s["rental"]["code"]
    wrong_type = client.post(f"/api/rentals/{code}/reservation", headers=DSP, json={"vehicle_id": s["van"]["id"]})
    assert wrong_type.json()["error"]["code"] == "VEHICLE_TYPE_MISMATCH"

    r = client.post(f"/api/rentals/{code}/reservation", headers=DSP, json={"vehicle_id": s["v1"]["id"]})
    assert r.status_code == 201
    rental = r.json()
    assert rental["status"] == "RESERVED"
    assert (rental["booking"]["planned_start"], rental["booking"]["planned_end"]) == (
        rental["requested_start"], rental["requested_end"])
    assert rental["booking"]["driver"] is None

    again = client.post(f"/api/rentals/{code}/reservation", headers=DSP, json={"vehicle_id": s["v2"]["id"]})
    assert again.json()["error"]["code"] == "INVALID_STATE"

    change = {"version": rental["version"], "vehicle_id": s["v2"]["id"], "planned_start": f.iso(9),
              "planned_end": f.iso(17), "reason": "Khách đổi giờ"}
    no_note = client.put(f"/api/rentals/{code}/reservation", headers=DSP, json=change)
    assert no_note.status_code == 422
    changed = client.put(f"/api/rentals/{code}/reservation", headers=DSP,
                         json={**change, "customer_note": "Đã gọi khách lúc 7:00"})
    assert changed.status_code == 200
    assert changed.json()["booking"]["vehicle"]["id"] == s["v2"]["id"]

    released = client.post(f"/api/rentals/{code}/reservation/release", headers=DSP, json={
        "version": changed.json()["version"], "reason": "Khách chưa chốt", "customer_note": "Hẹn gọi lại"})
    assert released.status_code == 200
    assert released.json()["status"] == "UNASSIGNED" and released.json()["booking"] is None


def test_rental_and_trip_cannot_take_same_vehicle(client, setup):
    """NEG-06: gán chuyến vào xe đã giữ cho thuê cùng giờ bị từ chối."""
    s = setup
    client.post(f"/api/rentals/{s['rental']['code']}/reservation", headers=DSP, json={"vehicle_id": s["v1"]["id"]})
    r = client.post(f"/api/trips/{s['trip']['code']}/assignment", headers=DSP, json=body(s["v1"], s["d1"], 9, 11))
    assert r.status_code == 409
    assert r.json()["error"]["details"]["conflicts"][0]["source_type"] == RENTAL


def test_cancel_releases_booking_in_same_transaction(conn, clock, setup):
    """Điểm nối SAL-09: hủy trước thực hiện thì giải phóng lịch."""
    s = setup
    now = clock()
    with write_tx(conn):
        assignments.reserve_rental(conn, s["rental"]["code"], vehicle_id=s["v1"]["id"], actor="dsp1", now=now)
        cancelled = assignments.cancel_operational_record(
            conn, source_type=RENTAL, source_code=s["rental"]["code"], reason="Khách hủy", actor="mgr1", now=now)
    assert cancelled["status"] == "CANCELLED"
    assert cancelled["booking"]["status"] == "RELEASED"


def test_cancel_refused_once_vehicle_is_in_use(conn, clock, setup):
    s = setup
    with write_tx(conn):
        trip = assignments.assign_trip(conn, s["trip"]["code"], vehicle_id=s["v1"]["id"], driver_id=s["d1"]["id"],
                                       planned_start=f.vn(8), planned_end=f.vn(10), actor="dsp1", now=clock())
    clock.set(f.vn(8, 30))
    with write_tx(conn):
        booking.start_usage(conn, trip["booking"]["id"], actual_start=f.vn(8, 15), actor="dsp1", now=clock())
    with pytest.raises(Conflict) as exc, write_tx(conn):
        assignments.cancel_operational_record(conn, source_type=TRANSPORT, source_code=s["trip"]["code"],
                                              reason="Khách hủy", actor="mgr1", now=clock())
    assert exc.value.code == "RESOURCE_IN_USE"
    assert booking.get_booking(conn, trip["booking"]["id"], clock())["status"] == "IN_USE"


def test_create_trip_for_same_order_returns_existing(conn, clock, setup):
    again = f.trip(conn, clock(), order="ORD-T-001", kg=1200)
    assert again["code"] == setup["trip"]["code"]


def test_errors_have_stable_shape_without_internals(client, setup):
    r = client.post("/api/trips/TRP-404/assignment", headers=DSP, json=body(setup["v1"], setup["d1"]))
    assert r.status_code == 404
    assert set(r.json()["error"]) == {"code", "message", "details"}
    assert "Traceback" not in r.text
