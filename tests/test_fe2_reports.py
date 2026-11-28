"""ADM-05/06: role isolation, date boundaries, drilldowns, CSV and audit."""

import csv
import io
from datetime import timedelta

import pytest

from backend import fe2_reports
from backend.db import write_tx
from backend.timeutil import to_db
from tests import factories as f
from tests.test_fe2_read import confirmed_order


def test_dashboard_count_matches_drilldown_and_date_end_is_inclusive(client, conn, clock):
    order = confirmed_order(conn, clock(), "trips")
    response = client.get("/api/fe2/dashboard?start=2026-12-01&end=2026-12-01", headers=f.MGR)
    assert response.status_code == 200, response.text
    metrics = {m["metric"]: m for m in response.json()["metrics"]}
    assert metrics["orders_new"]["value"] == 1
    assert metrics["orders_new"]["period"] is True
    detail = client.get("/api/fe2/dashboard/orders_new?start=2026-12-01&end=2026-12-01", headers=f.MGR).json()
    assert detail["total"] == detail["value"] == 1
    assert detail["items"][0]["code"] == order["code"]
    assert client.get("/api/fe2/dashboard/orders_new?start=2026-11-30&end=2026-11-30", headers=f.MGR).json()["total"] == 0
    assert client.get("/api/fe2/dashboard?start=2026-12-02&end=2026-12-01", headers=f.MGR).status_code == 422


@pytest.mark.parametrize("role,forbidden", [(f.DSP, "receivables_balance"), (f.WHS, "receivables_balance"), (f.FIN, "trips_active")])
def test_roles_hide_and_reject_other_metrics(client, role, forbidden):
    response = client.get("/api/fe2/dashboard", headers=role)
    assert response.status_code == 200
    assert forbidden not in {m["metric"] for m in response.json()["metrics"]}
    assert client.get(f"/api/fe2/dashboard/{forbidden}", headers=role).status_code == 403
    assert client.get("/api/fe2/reports/receivables.csv" if forbidden == "receivables_balance" else "/api/fe2/reports/schedule.csv", headers=role).status_code == 403


def test_csv_all_rows_safe_values_and_audit(client, conn, clock):
    confirmed_order(conn, clock(), "trips")
    response = client.get("/api/fe2/reports/orders.csv?start=2026-12-01&end=2026-12-01", headers=f.MGR)
    assert response.status_code == 200
    assert "attachment;" in response.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))
    assert len(rows) == 1
    assert rows[0]["service_type"] == "TRANSPORT"
    audit = conn.execute("SELECT action,object_code FROM audit_log WHERE action='REPORT_EXPORT'").fetchone()
    assert dict(audit) == {"action": "REPORT_EXPORT", "object_code": "orders"}
    assert fe2_reports.csv_safe('=1+1') == "'=1+1"
    assert fe2_reports.csv_safe(' +SUM(1)') == "' +SUM(1)"


def test_snapshot_ignores_period_and_finance_is_not_double_counted(client, conn, clock):
    order = confirmed_order(conn, clock(), "rentals")
    snapshot = client.get("/api/fe2/dashboard/receivables_balance?start=2020-01-01&end=2020-01-01", headers=f.FIN).json()
    assert snapshot["value"] == 3_000_000
    assert snapshot["total"] == 1
    assert snapshot["period"] is False
    csv_response = client.get("/api/fe2/reports/receivables.csv?start=2020-01-01&end=2020-01-01", headers=f.FIN)
    assert csv_response.status_code == 200
    assert order["code"] in csv_response.text


def test_unauthorized_and_unknown_report(client):
    assert client.get("/api/fe2/dashboard").status_code == 401
    assert client.get("/api/fe2/dashboard", headers=f.ADM).status_code == 403
    assert client.get("/api/fe2/reports/unknown.csv", headers=f.MGR).status_code == 422


def test_rental_overdue_uses_booked_end_not_requested_end(client, conn, clock):
    rental = f.rental(conn, clock())
    vehicle = f.vehicle(conn, clock())
    start = to_db(clock() - timedelta(hours=3))
    end = to_db(clock() - timedelta(hours=2))
    with write_tx(conn):
        booking_id = conn.execute(
            "INSERT INTO resource_bookings(code,source_type,source_code,vehicle_id,planned_start,planned_end,status,actual_start,created_at,created_by,updated_at,updated_by) "
            "VALUES(?,?,?,?,?,?,'IN_USE',?,?,?,?,?)",
            ("BK-UAT-OVERDUE", "RENTAL", rental["code"], vehicle["id"], start, end, start,
             start, "dsp1", start, "dsp1"),
        ).lastrowid
        conn.execute("UPDATE rental_records SET booking_id=?,status='HANDED_OVER' WHERE code=?",
                     (booking_id, rental["code"]))
    result = client.get("/api/fe2/dashboard/rentals_overdue", headers=f.DSP)
    assert result.status_code == 200, result.text
    assert result.json()["value"] == 1
    assert result.json()["items"][0]["code"] == rental["code"]
    assert result.json()["items"][0]["planned_end"] == end
