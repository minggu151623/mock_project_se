"""RNT-03/04/05 qua API: bàn giao, trả xe, hoàn tất. E2E-02, NEG-11, BR-09."""

import sqlite3

import pytest

from backend import integrations
from tests import factories as f
from tests.factories import DSP


@pytest.fixture
def ctx(client, conn, clock):
    now = clock()
    v = f.vehicle(conn, now, plate="51C-800.01", capacity=2000, vtype="TRUCK_2T")
    rental = f.rental(conn, now, order="ORD-R-001", vtype="TRUCK_2T", min_kg=1000, start=f.vn(8), end=f.vn(18))
    r = client.post(f"/api/rentals/{rental['code']}/reservation", headers=DSP, json={"vehicle_id": v["id"]})
    assert r.status_code == 201
    return {"v": v, "code": rental["code"], "order": rental["order_code"]}


def inspection(client, hour, minute=0, km=1000, **extra):
    return {"actual_at": f.iso(hour, minute), "odometer_km": km, "body_condition": "Không trầy xước",
            "fuel_level": "3/4 bình", "accessories": "Kích, lốp dự phòng", "attachment_codes": [f.upload(client)],
            **extra}


def handover(client, code, hour=8, minute=5, **extra):
    return client.post(f"/api/rentals/{code}/handover", headers=DSP,
                       json={**inspection(client, hour, minute, **extra), "receiver_name": "Anh Minh"})


def give_back(client, code, hour, minute=0, km=1200):
    return client.post(f"/api/rentals/{code}/return", headers=DSP,
                       json={**inspection(client, hour, minute, km=km), "returned_by": "Anh Minh"})


def test_late_return_flow_e2e_02(client, conn, clock, ctx):
    """E2E-02: quá giờ hẹn trả mà chưa nhận lại thì xe vẫn bận; phụ phí chờ chặn hoàn tất."""
    events = []
    integrations.hooks.operation_started = lambda c, o, s, at: events.append(("started", o))
    integrations.hooks.operation_completed = lambda c, o, s, at: events.append(("completed", o))
    code = ctx["code"]
    nxt = f.rental(conn, clock(), order="ORD-R-002", start=f.vn(18), end=f.vn(20))
    client.post(f"/api/rentals/{nxt['code']}/reservation", headers=DSP, json={"vehicle_id": ctx["v"]["id"]})

    clock.set(f.vn(8, 10))
    r = handover(client, code)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "HANDED_OVER" and r.json()["handover"]["odometer_km"] == 1000

    clock.set(f.vn(18, 30))
    late = client.get(f"/api/rentals/{code}", headers=DSP).json()
    assert late["overdue"] is True and late["late_minutes"] == 30
    blocked = handover(client, nxt["code"], 18, 30)
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "RESOURCE_STILL_IN_USE"

    clock.set(f.vn(18, 45))
    back = give_back(client, code, 18, 40).json()
    assert back["status"] == "RETURNED" and back["late_minutes"] == 40
    assert back["booking"]["status"] == "RELEASED"

    integrations.hooks.pending_charge_count = lambda c, order: 1
    pending = client.post(f"/api/rentals/{code}/complete", headers=DSP)
    assert pending.status_code == 409 and pending.json()["error"]["code"] == "PENDING_CHARGES"
    integrations.hooks.pending_charge_count = lambda c, order: 0
    assert client.post(f"/api/rentals/{code}/complete", headers=DSP).json()["status"] == "COMPLETED"
    assert events == [("started", ctx["order"]), ("completed", ctx["order"])]

    assert handover(client, nxt["code"], 18, 45).status_code == 200


def test_handover_rules(client, clock, ctx):
    clock.set(f.vn(7, 30))
    outside = handover(client, ctx["code"], 7, 30)
    assert outside.status_code == 409 and outside.json()["error"]["code"] == "OUTSIDE_BOOKED_WINDOW"
    clock.set(f.vn(8, 10))
    no_file = client.post(f"/api/rentals/{ctx['code']}/handover", headers=DSP,
                          json={**inspection(client, 8, 5), "attachment_codes": [], "receiver_name": "A"})
    assert no_file.status_code == 422
    assert handover(client, ctx["code"]).status_code == 200
    again = handover(client, ctx["code"])
    assert again.status_code == 409 and again.json()["error"]["code"] == "INVALID_STATE"


def test_return_rules_neg_11(client, clock, ctx):
    clock.set(f.vn(8, 10))
    handover(client, ctx["code"])
    clock.set(f.vn(17))
    lower_km = give_back(client, ctx["code"], 16, km=999)
    assert lower_km.status_code == 422 and lower_km.json()["error"]["details"]["field"] == "odometer_km"
    before = give_back(client, ctx["code"], 8, 0)
    assert before.status_code == 422 and before.json()["error"]["details"]["field"] == "actual_at"
    future = give_back(client, ctx["code"], 17, 30)
    assert future.status_code == 422
    ok = give_back(client, ctx["code"], 16, km=1000).json()
    assert ok["status"] == "RETURNED" and ok["late_minutes"] == 0


def test_inspection_immutable_but_notes_append(client, conn, clock, ctx):
    clock.set(f.vn(8, 10))
    handover(client, ctx["code"])
    r = client.post(f"/api/rentals/{ctx['code']}/notes", headers=DSP, json={"note": "Khách báo đèn phanh yếu"})
    assert r.status_code == 201
    assert r.json()["notes"][0]["author"] == "dsp1"
    with pytest.raises(sqlite3.IntegrityError, match="INSPECTION_IMMUTABLE"):
        conn.execute("UPDATE rental_inspections SET odometer_km = 0")


def test_complete_requires_return(client, clock, ctx):
    r = client.post(f"/api/rentals/{ctx['code']}/complete", headers=DSP)
    assert r.status_code == 409 and r.json()["error"]["code"] == "INVALID_STATE"


def test_rental_list_overdue_filter(client, clock, ctx):
    clock.set(f.vn(8, 10))
    handover(client, ctx["code"])
    clock.set(f.vn(19))
    listed = client.get("/api/rentals", headers=DSP, params={"overdue_only": True}).json()
    assert [x["code"] for x in listed["items"]] == [ctx["code"]]
