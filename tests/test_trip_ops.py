"""TRN-03/04/05 qua API: nhận, vận chuyển, giao, hoàn tất, sự cố. NEG-09, NEG-10, BR-09."""

import pytest

from backend import integrations
from backend.attachments import link
from backend.db import write_tx
from backend.errors import Conflict
from tests import factories as f
from tests.factories import ADM, DSP, SAL, WHS


@pytest.fixture
def ctx(client, conn, clock):
    now = clock()
    v = f.vehicle(conn, now, plate="51C-700.01", capacity=2000)
    d = f.driver(conn, now)
    trip = f.trip(conn, now, kg=1200, committed_end=f.vn(12))
    r = client.post(f"/api/trips/{trip['code']}/assignment", headers=DSP, json={
        "vehicle_id": v["id"], "driver_id": d["id"], "planned_start": f.iso(8), "planned_end": f.iso(10)})
    assert r.status_code == 201
    return {"v": v, "d": d, "trip": trip["code"], "order": trip["order_code"]}


def post(client, path, body, headers=DSP):
    return client.post(path, headers=headers, json=body)


def pickup_body(hour=8, minute=5, **extra):
    return {"actual_at": f.iso(hour, minute), "handed_over_by": "Kho KH-001", "cargo_condition": "60 kiện nguyên đai",
            **extra}


def to_in_transit(client, clock, code):
    clock.set(f.vn(8, 10))
    assert post(client, f"/api/trips/{code}/pickup", pickup_body()).status_code == 200
    clock.set(f.vn(8, 30))
    assert post(client, f"/api/trips/{code}/in-transit", {"actual_at": f.iso(8, 20)}).status_code == 200


def test_full_trip_flow(client, clock, ctx):
    events = []
    integrations.hooks.operation_started = lambda c, o, s, at: events.append(("started", o, s))
    integrations.hooks.operation_completed = lambda c, o, s, at: events.append(("completed", o, s))
    code = ctx["trip"]
    to_in_transit(client, clock, code)
    trip = client.get(f"/api/trips/{code}", headers=DSP).json()
    assert trip["status"] == "IN_TRANSIT" and trip["booking"]["status"] == "IN_USE"

    clock.set(f.vn(9, 45))
    att = f.upload(client)
    r = post(client, f"/api/trips/{code}/delivery", {"actual_at": f.iso(9, 40), "result": "SUCCESS",
                                                    "receiver_name": "Chị Lan", "attachment_codes": [att]})
    assert r.status_code == 200 and r.json()["status"] == "DELIVERED"

    clock.set(f.vn(10))
    done = post(client, f"/api/trips/{code}/complete", {"released_at": f.iso(9, 50)}).json()
    assert done["status"] == "COMPLETED"
    assert done["booking"]["status"] == "RELEASED"
    assert done["booking"]["actual_end"] == "2026-12-01T02:50:00Z"
    assert [m["milestone"] for m in done["milestones"]] == ["PICKED_UP", "IN_TRANSIT", "DELIVERED", "COMPLETED"]
    assert done["delivery_evidence"][0]["code"] == att
    assert events == [("started", ctx["order"], "TRANSPORT"), ("completed", ctx["order"], "TRANSPORT")]
    free = client.get("/api/availability/vehicles", headers=DSP,
                      params={"start": f.iso(10), "end": f.iso(12), "min_capacity_kg": 1000}).json()["items"]
    assert ctx["v"]["id"] in {x["id"] for x in free}


def test_states_cannot_be_skipped(client, conn, clock, ctx):
    """NEG-09: bỏ qua trạng thái bị từ chối."""
    fresh = f.trip(conn, clock(), order="ORD-T-009")["code"]
    att = f.upload(client)
    r = post(client, f"/api/trips/{fresh}/delivery", {"actual_at": f.iso(6), "result": "SUCCESS",
                                                     "receiver_name": "A", "attachment_codes": [att]})
    assert r.status_code == 409 and r.json()["error"]["code"] == "INVALID_STATE"
    assert post(client, f"/api/trips/{ctx['trip']}/in-transit", {"actual_at": f.iso(6)}).status_code == 409
    clock.set(f.vn(8, 10))
    post(client, f"/api/trips/{ctx['trip']}/pickup", pickup_body())
    r = post(client, f"/api/trips/{ctx['trip']}/delivery", {"actual_at": f.iso(8, 9), "result": "SUCCESS",
                                                           "receiver_name": "A", "attachment_codes": [att]})
    assert r.json()["error"]["code"] == "INVALID_STATE"


def test_delivery_needs_evidence_and_time_order(client, clock, ctx):
    """NEG-09: thiếu bằng chứng hoặc giao trước lúc vận chuyển bị từ chối."""
    code = ctx["trip"]
    to_in_transit(client, clock, code)
    clock.set(f.vn(9, 45))
    no_file = post(client, f"/api/trips/{code}/delivery", {"actual_at": f.iso(9, 40), "result": "SUCCESS",
                                                          "receiver_name": "Chị Lan"})
    assert no_file.status_code == 422 and no_file.json()["error"]["details"]["field"] == "attachment_codes"
    att = f.upload(client)
    too_early = post(client, f"/api/trips/{code}/delivery", {"actual_at": f.iso(8, 15), "result": "SUCCESS",
                                                            "receiver_name": "Chị Lan", "attachment_codes": [att]})
    assert too_early.status_code == 422 and too_early.json()["error"]["details"]["field"] == "actual_at"
    future = post(client, f"/api/trips/{code}/delivery", {"actual_at": f.iso(10), "result": "SUCCESS",
                                                         "receiver_name": "Chị Lan", "attachment_codes": [att]})
    assert future.status_code == 422
    assert client.get(f"/api/trips/{code}", headers=DSP).json()["status"] == "IN_TRANSIT"


def test_failed_delivery_keeps_in_transit_and_opens_incident(client, clock, ctx):
    code = ctx["trip"]
    to_in_transit(client, clock, code)
    clock.set(f.vn(9, 45))
    need_note = post(client, f"/api/trips/{code}/delivery", {"actual_at": f.iso(9, 40), "result": "FAILED"})
    assert need_note.status_code == 422
    r = post(client, f"/api/trips/{code}/delivery", {"actual_at": f.iso(9, 40), "result": "FAILED",
                                                    "note": "Người nhận vắng nhà"}).json()
    assert r["status"] == "IN_TRANSIT"
    assert r["incidents"][0]["incident_type"] == "DELIVERY_FAILED" and r["open_incidents"] == 1
    assert "DELIVERED" not in [m["milestone"] for m in r["milestones"]]


def test_over_capacity_pickup_stays_assigned(client, clock, ctx):
    clock.set(f.vn(8, 10))
    r = post(client, f"/api/trips/{ctx['trip']}/pickup", pickup_body(actual_weight_kg=2500))
    assert r.status_code == 409 and r.json()["error"]["code"] == "OVER_CAPACITY"
    trip = client.get(f"/api/trips/{ctx['trip']}", headers=DSP).json()
    assert trip["status"] == "ASSIGNED" and trip["booking"]["status"] == "HELD"


def test_next_pickup_blocked_while_vehicle_busy(client, conn, clock, ctx):
    """BR-09, NEG-10: chuyến trước chưa xong thì chưa nhận hàng chuyến sau dù đã tới giờ."""
    second = f.trip(conn, clock(), order="ORD-T-002", kg=500)["code"]
    d2 = f.driver(conn, clock(), name="Tài xế 2", phone="0907654321")
    post(client, f"/api/trips/{second}/assignment", {"vehicle_id": ctx["v"]["id"], "driver_id": d2["id"],
                                                     "planned_start": f.iso(10), "planned_end": f.iso(12)})
    clock.set(f.vn(8, 10))
    post(client, f"/api/trips/{ctx['trip']}/pickup", pickup_body())
    clock.set(f.vn(10, 30))
    r = post(client, f"/api/trips/{second}/pickup", pickup_body(10, 30))
    assert r.status_code == 409 and r.json()["error"]["code"] == "RESOURCE_STILL_IN_USE"
    assert client.get(f"/api/trips/{second}", headers=DSP).json()["status"] == "ASSIGNED"
    assert client.get(f"/api/trips/{ctx['trip']}", headers=DSP).json()["booking"]["overdue"] is True


def test_resubmitted_pickup_does_not_duplicate(client, clock, ctx):
    clock.set(f.vn(8, 10))
    headers = {**DSP, "Idempotency-Key": "pickup-1"}
    first = client.post(f"/api/trips/{ctx['trip']}/pickup", headers=headers, json=pickup_body())
    again = client.post(f"/api/trips/{ctx['trip']}/pickup", headers=headers, json=pickup_body())
    assert first.status_code == again.status_code == 200
    assert len(again.json()["milestones"]) == 1
    no_key = post(client, f"/api/trips/{ctx['trip']}/pickup", pickup_body())
    assert no_key.json()["error"]["code"] == "INVALID_STATE"


def test_completion_rules(client, clock, ctx):
    code = ctx["trip"]
    to_in_transit(client, clock, code)
    clock.set(f.vn(9, 45))
    post(client, f"/api/trips/{code}/delivery", {"actual_at": f.iso(9, 40), "result": "SUCCESS",
                                                 "receiver_name": "Chị Lan", "attachment_codes": [f.upload(client)]})
    before = post(client, f"/api/trips/{code}/complete", {"released_at": f.iso(9, 30)})
    assert before.status_code == 422 and before.json()["error"]["details"]["field"] == "released_at"
    integrations.hooks.pending_charge_count = lambda c, order: 1
    pending = post(client, f"/api/trips/{code}/complete", {"released_at": f.iso(9, 45)})
    assert pending.status_code == 409 and pending.json()["error"]["code"] == "PENDING_CHARGES"
    integrations.reset()
    assert post(client, f"/api/trips/{code}/complete", {"released_at": f.iso(9, 45)}).json()["status"] == "COMPLETED"


def test_incident_lifecycle_and_overdue_flag(client, clock, ctx):
    code = ctx["trip"]
    to_in_transit(client, clock, code)
    r = client.post(f"/api/trips/{code}/incidents", headers=DSP, json={
        "incident_type": "DELAY", "occurred_at": f.iso(8, 25), "description": "Kẹt xe QL1",
        "handler_name": "Điều phối A", "expected_resolution_at": f.iso(11)})
    assert r.status_code == 201
    incident = r.json()["incidents"][0]
    assert incident["status"] == "OPEN" and r.json()["status"] == "IN_TRANSIT"

    clock.set(f.vn(12, 30))  # quá giờ kết thúc cam kết 12:00
    assert client.get(f"/api/trips/{code}", headers=DSP).json()["overdue"] is True
    overdue = client.get("/api/trips", headers=DSP, params={"overdue_only": True}).json()
    assert [t["code"] for t in overdue["items"]] == [code]

    resolved = client.post(f"/api/incidents/{incident['code']}/resolve", headers=DSP, json={
        "version": incident["version"], "resolution": "Đã thông đường", "resolved_at": f.iso(12)}).json()
    assert resolved["open_incidents"] == 0
    again = client.post(f"/api/incidents/{incident['code']}/resolve", headers=DSP, json={
        "version": incident["version"] + 1, "resolution": "x", "resolved_at": f.iso(12)})
    assert again.status_code == 409


def test_attachment_rules(client, clock, ctx, conn):
    fake = client.post("/api/attachments", headers=DSP, files={"file": ("anh.png", b"MZ not an image", "image/png")})
    assert fake.status_code == 422
    big = client.post("/api/attachments", headers=DSP,
                      files={"file": ("lon.pdf", b"%PDF-" + b"0" * (5 * 1024 * 1024), "application/pdf")})
    assert big.status_code == 422
    assert client.post("/api/attachments", headers=SAL, files={"file": ("a.png", f.PNG, "image/png")}).status_code == 403
    pdf = f.upload(client, headers=WHS, name="phieu.pdf", data=f.PDF)
    got = client.get(f"/api/attachments/{pdf}", headers=SAL)
    assert got.status_code == 200 and got.content == f.PDF
    assert got.headers["content-type"] == "application/pdf"
    assert client.get(f"/api/attachments/{pdf}", headers=ADM).status_code == 403

    # Tệp đã làm bằng chứng cho chuyến này thì không gắn sang chuyến khác.
    code = ctx["trip"]
    to_in_transit(client, clock, code)
    clock.set(f.vn(9, 45))
    att = f.upload(client)
    post(client, f"/api/trips/{code}/delivery", {"actual_at": f.iso(9, 40), "result": "SUCCESS",
                                                 "receiver_name": "A", "attachment_codes": [att]})
    with pytest.raises(Conflict) as exc, write_tx(conn):
        link(conn, [att], owner_type="TRIP_DELIVERY", owner_code="TRP-OTHER", actor="x", now=clock())
    assert exc.value.code == "ATTACHMENT_ALREADY_USED"
