"""TRN-01 qua API: xe, tài xế, phân quyền, đổi trạng thái khi còn lịch (NEG-08)."""

from backend import booking
from backend.db import write_tx
from tests import factories as f
from tests.factories import ADM, DSP, SAL


def new_vehicle(client, plate="51C-123.45", capacity=2000, **extra):
    return client.post("/api/vehicles", headers=DSP,
                       json={"plate_number": plate, "vehicle_type": "TRUCK_2T", "capacity_kg": capacity, **extra})


def assign(client, trip_code, vehicle_id, driver_id, start=8, end=10):
    return client.post(f"/api/trips/{trip_code}/assignment", headers=DSP, json={
        "vehicle_id": vehicle_id, "driver_id": driver_id,
        "planned_start": f.iso(start), "planned_end": f.iso(end)})


def test_plate_normalized_and_duplicates_rejected(client):
    r = new_vehicle(client, plate=" 51c-123.45 ")
    assert r.status_code == 201
    assert r.json()["plate_number"] == "51C-123.45"
    dup = new_vehicle(client, plate="51C 12345")
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "DUPLICATE_PLATE"


def test_invalid_input_names_the_field(client):
    bad_plate = new_vehicle(client, plate="@@")
    assert bad_plate.status_code == 422
    assert bad_plate.json()["error"]["details"]["field"] == "plate_number"
    zero = new_vehicle(client, capacity=0)
    assert zero.status_code == 422
    assert zero.json()["error"]["details"]["fields"][0]["field"] == "capacity_kg"


def test_driver_code_generated_and_phone_normalized(client):
    r = client.post("/api/drivers", headers=DSP, json={"full_name": "  Trần Văn B ", "phone": "+84 90-123 4567"})
    assert r.status_code == 201
    assert r.json()["code"] == "DRV-0001"
    assert r.json()["full_name"] == "Trần Văn B"
    assert r.json()["phone"] == "+84901234567"
    bad = client.post("/api/drivers", headers=DSP, json={"full_name": "C", "phone": "12345"})
    assert bad.json()["error"]["details"]["field"] == "phone"


def test_roles_enforced_on_every_call(client):
    assert client.get("/api/vehicles").status_code == 401
    assert new_vehicle(client).status_code == 201
    assert client.post("/api/vehicles", headers=SAL, json={
        "plate_number": "51C-999.99", "vehicle_type": "TRUCK_2T", "capacity_kg": 1000}).status_code == 403
    assert client.get("/api/vehicles", headers=SAL).status_code == 200
    # ADM không mặc nhiên có quyền nghiệp vụ (SRS mục 2).
    assert client.get("/api/vehicles", headers=ADM).status_code == 403


def test_status_change_blocked_by_held_booking(client, conn, clock):
    v = new_vehicle(client).json()
    d = f.driver(conn, clock())
    trip = f.trip(conn, clock())
    assert assign(client, trip["code"], v["id"], d["id"]).status_code == 201

    r = client.post(f"/api/vehicles/{v['id']}/status", headers=DSP,
                    json={"version": v["version"], "status": "MAINTENANCE", "reason": "Thay lốp"})
    assert r.status_code == 409
    err = r.json()["error"]
    assert err["code"] == "FUTURE_BOOKINGS_EXIST"
    assert err["details"]["bookings"][0]["source_code"] == trip["code"]

    d_r = client.post(f"/api/drivers/{d['id']}/status", headers=DSP,
                      json={"version": d["version"], "status": "INACTIVE"})
    assert d_r.json()["error"]["code"] == "FUTURE_BOOKINGS_EXIST"

    current = client.get(f"/api/trips/{trip['code']}", headers=DSP).json()
    client.post(f"/api/trips/{trip['code']}/assignment/release", headers=DSP,
                json={"version": current["version"], "reason": "Đổi lịch"})
    ok = client.post(f"/api/vehicles/{v['id']}/status", headers=DSP,
                     json={"version": v["version"], "status": "MAINTENANCE", "reason": "Thay lốp"})
    assert ok.status_code == 200 and ok.json()["status"] == "MAINTENANCE"


def test_vehicle_in_use_cannot_be_deactivated(client, conn, clock):
    v = new_vehicle(client).json()
    d = f.driver(conn, clock())
    trip = f.trip(conn, clock())
    b = assign(client, trip["code"], v["id"], d["id"]).json()["booking"]
    clock.set(f.vn(8, 10))
    with write_tx(conn):
        booking.start_usage(conn, b["id"], actual_start=f.vn(8, 5), actor="dsp1", now=clock())
    r = client.post(f"/api/vehicles/{v['id']}/status", headers=DSP,
                    json={"version": v["version"], "status": "INACTIVE"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "RESOURCE_IN_USE"
    assert client.get(f"/api/vehicles/{v['id']}", headers=DSP).json()["in_use"] is True


def test_capacity_cannot_drop_below_booked_cargo(client, conn, clock):
    v = new_vehicle(client).json()
    d = f.driver(conn, clock())
    trip = f.trip(conn, clock(), kg=1200)
    assign(client, trip["code"], v["id"], d["id"])
    r = client.patch(f"/api/vehicles/{v['id']}", headers=DSP, json={"version": v["version"], "capacity_kg": 1000})
    assert r.status_code == 409 and r.json()["error"]["code"] == "CAPACITY_BELOW_BOOKINGS"
    ok = client.patch(f"/api/vehicles/{v['id']}", headers=DSP, json={"version": v["version"], "capacity_kg": 1500})
    assert ok.status_code == 200 and ok.json()["capacity_kg"] == 1500


def test_stale_version_is_rejected(client):
    v = new_vehicle(client).json()
    first = client.patch(f"/api/vehicles/{v['id']}", headers=DSP, json={"version": v["version"], "description": "A"})
    assert first.status_code == 200
    second = client.patch(f"/api/vehicles/{v['id']}", headers=DSP, json={"version": v["version"], "description": "B"})
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "VERSION_CONFLICT"
    assert client.get(f"/api/vehicles/{v['id']}", headers=DSP).json()["description"] == "A"


def test_availability_respects_schedule_status_and_capacity(client, conn, clock):
    busy = new_vehicle(client, plate="51C-000.01").json()
    free = new_vehicle(client, plate="51C-000.02").json()
    small = new_vehicle(client, plate="51C-000.03", capacity=500).json()
    new_vehicle(client, plate="51C-000.04", status="MAINTENANCE")
    d = f.driver(conn, clock())
    assign(client, f.trip(conn, clock())["code"], busy["id"], d["id"], 8, 10)

    def ids(start, end, kg):
        r = client.get("/api/availability/vehicles", headers=DSP,
                       params={"start": f.iso(start), "end": f.iso(end), "min_capacity_kg": kg})
        return {item["id"] for item in r.json()["items"]}

    assert ids(9, 11, 1000) == {free["id"]}
    assert ids(10, 12, 1000) == {busy["id"], free["id"]}  # liền nhau không chồng
    assert ids(10, 12, 100) == {busy["id"], free["id"], small["id"]}

    drivers = client.get("/api/availability/drivers", headers=DSP,
                         params={"start": f.iso(9), "end": f.iso(11)}).json()["items"]
    assert drivers == []


def test_list_is_paginated(client):
    for i in range(3):
        new_vehicle(client, plate=f"51C-555.0{i}")
    page = client.get("/api/vehicles", headers=DSP, params={"limit": 2, "offset": 0}).json()
    assert page["total"] == 3 and len(page["items"]) == 2
    found = client.get("/api/vehicles", headers=DSP, params={"q": "51c 55501"}).json()
    assert [v["plate_number"] for v in found["items"]] == ["51C-555.01"]
