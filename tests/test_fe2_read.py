"""FE2 integration reads: financial summaries and shared resource schedules."""

import pytest

from backend import assignments, sales
from backend.db import write_tx
from tests import factories as f
from tests.test_sales import customer


def confirmed_order(conn, now, kind):
    c = customer(conn, now)
    data = ({"pickup_address": "Kho", "delivery_address": "Diem giao", "cargo_description": "Hang",
             "cargo_weight_kg": 1000, "planned_pickup_at": f.iso(8)} if kind == "trips" else
            {"vehicle_type": "TRUCK_2T", "min_capacity_kg": 1000,
             "requested_start": f.iso(8), "requested_end": f.iso(10), "purpose": "Cho hang"})
    with write_tx(conn):
        request = sales.create_request(conn, service_type="TRANSPORT" if kind == "trips" else "RENTAL",
                                       source="INTERNAL", contact={"name": c["name"], "contact_name": c["contact_name"], "phone": c["phone"]},
                                       service_data=data, actor="sal1", now=now)
        sales.update_request(conn, request["code"], version=request["version"],
                             customer_code=c["code"], actor="sal1", now=now)
        quote = sales.create_quote(conn, request_code=request["code"], valid_until="2026-12-02",
                                   service_data=data, terms="Thu sau", lines=[{"description": "Cuoc",
                                   "quantity": 1, "unit_price_vnd": 3_000_000}], actor="sal1", now=now)
        sales.issue_quote(conn, quote["code"], actor="sal1", now=now)
        return sales.respond_quote_and_create_order(conn, quote["code"], accepted=True, channel="PHONE",
                content="Dong y", evidence=[], payment_due_date="2026-12-10", actor="sal1", now=now)["order"]


@pytest.mark.parametrize("kind", ["trips", "rentals"])
@pytest.mark.parametrize("role", [f.DSP, f.SAL, f.MGR])
def test_summary_shows_approved_rejected_and_pending_without_double_count(client, conn, clock, kind, role):
    order = confirmed_order(conn, clock(), kind)
    for status, amount in [("APPROVED", 300_000), ("REJECTED", 200_000), ("PENDING", 100_000)]:
        evidence = f.upload(client)
        with write_tx(conn):
            charge = sales.propose_charge(conn, order["code"], amount_vnd=amount, reason="Phat sinh",
                                          evidence=[evidence], roles={"DSP"}, actor="dsp1", now=clock())
            if status != "PENDING":
                sales.decide_charge(conn, charge["code"], approved=status == "APPROVED",
                                    decision_note="Da kiem tra", actor="mgr1", now=clock())
    response = client.get(f"/api/fe2/{kind}/{order['operational_code']}/commercial-summary", headers=role)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["pending_charges"] == 1
    assert [c["status"] for c in result["charges"]] == ["APPROVED", "REJECTED", "PENDING"]
    assert result["charges"][0]["decided_by"] == "mgr1"
    assert result["balance"]["amount_due_vnd"] == 3_300_000
    assert result["balance"]["balance_vnd"] == 3_300_000
    assert result["balance"]["payment_status"] == "UNPAID"
    assert not {"payments", "customer_snapshot", "service_snapshot", "evidence_json"} & result.keys()


@pytest.mark.parametrize("role", [f.ADM, f.WHS, f.FIN])
@pytest.mark.parametrize("path", ["/api/fe2/trips/TRP-000001/commercial-summary",
                                  "/api/fe2/rentals/RNT-000001/commercial-summary",
                                  "/api/fe2/schedule?start=2026-12-01T00:00:00Z&end=2026-12-02T00:00:00Z"])
def test_reads_reject_wrong_roles_before_returning_data(client, role, path):
    assert client.get(path, headers=role).status_code == 403


def test_missing_order_has_no_invented_financial_state(client, conn, clock):
    rental = f.rental(conn, clock())
    response = client.get(f"/api/fe2/rentals/{rental['code']}/commercial-summary", headers=f.DSP)
    assert response.status_code == 200
    assert response.json()["balance"] is None
    assert response.json()["pending_charges"] is None
    assert not response.json()["available"]
    assert client.get("/api/fe2/rentals/missing/commercial-summary", headers=f.DSP).status_code == 404
    assert client.get("/api/fe2/warehouse/LOT-1/commercial-summary", headers=f.DSP).status_code == 422


def test_schedule_has_orders_service_types_and_preserves_filter_pagination(client, conn, clock):
    v = f.vehicle(conn, clock())
    d = f.driver(conn, clock())
    trip = f.trip(conn, clock())
    rental = f.rental(conn, clock(), start=f.vn(10), end=f.vn(12))
    with write_tx(conn):
        assignments.assign_trip(conn, trip["code"], vehicle_id=v["id"], driver_id=d["id"],
                                planned_start=f.vn(8), planned_end=f.vn(10), actor="dsp1", now=clock())
        assignments.reserve_rental(conn, rental["code"], vehicle_id=v["id"], actor="dsp1", now=clock())
    params = {"start": f.iso(7), "end": f.iso(13), "vehicle_id": v["id"], "limit": 1}
    page1 = client.get("/api/fe2/schedule", params=params, headers=f.DSP).json()
    page2 = client.get("/api/fe2/schedule", params={**params, "offset": 1}, headers=f.DSP).json()
    assert page1["total"] == page2["total"] == 2
    assert page1["items"][0]["order_code"] == trip["order_code"]
    assert page1["items"][0]["source_type"] == "TRANSPORT"
    assert page2["items"][0]["order_code"] == rental["order_code"]
    assert page2["items"][0]["source_type"] == "RENTAL"
    by_driver = client.get("/api/fe2/schedule", params={**params, "driver_id": d["id"]}, headers=f.DSP).json()
    assert by_driver["total"] == 1
    with write_tx(conn):
        current = assignments.get_trip(conn, trip["code"], clock())
        assignments.unassign_trip(conn, trip["code"], expected_version=current["version"],
                                  reason="Doi ke hoach", actor="dsp1", now=clock())
    assert client.get("/api/fe2/schedule", params=params, headers=f.DSP).json()["total"] == 1
    assert client.get("/api/fe2/schedule", params={**params, "include_released": True}, headers=f.DSP).json()["total"] == 2


def test_fe2_reads_require_login_and_do_not_grant_sales_access(client):
    assert client.get("/api/fe2/trips/TRP-1/commercial-summary").status_code == 401
    assert client.get("/api/sales/orders/ORD-1", headers=f.DSP).status_code == 403
