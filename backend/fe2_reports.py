"""Role-scoped FE2 report queries. Counts and drilldowns share one query per metric."""

import csv
import io
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .errors import Forbidden, ValidationFailed
from .timeutil import to_db

TZ = ZoneInfo("Asia/Ho_Chi_Minh")
ROLES = frozenset({"MGR", "SAL", "DSP", "WHS", "FIN"})
ORDER_READ = frozenset({"MGR", "SAL", "FIN"})
REQUEST_READ = frozenset({"MGR", "SAL"})
FINANCE = frozenset({"MGR", "SAL", "FIN"})
FLEET = frozenset({"MGR", "DSP"})
WAREHOUSE = frozenset({"MGR", "WHS"})

METRICS = {
    "requests_new": ("Yêu cầu mới trong kỳ", REQUEST_READ, True, "created_at", "SELECT code,status,service_type,created_at FROM service_requests WHERE status='NEW'"),
    "requests_unassigned": ("Yêu cầu chưa phân công", REQUEST_READ, False, None, "SELECT code,status,service_type,created_at FROM service_requests WHERE assigned_to IS NULL AND status NOT IN ('CONFIRMED','CLOSED_LOST')"),
    "orders_new": ("Đơn xác nhận trong kỳ", ORDER_READ, True, "created_at", "SELECT code,status,service_type,customer_code,created_at FROM service_orders"),
    "orders_transport": ("Đơn vận tải", ORDER_READ, False, None, "SELECT code,status,service_type,customer_code,created_at FROM service_orders WHERE service_type='TRANSPORT'"),
    "orders_rental": ("Đơn thuê xe", ORDER_READ, False, None, "SELECT code,status,service_type,customer_code,created_at FROM service_orders WHERE service_type='RENTAL'"),
    "orders_warehouse": ("Đơn kho", ORDER_READ, False, None, "SELECT code,status,service_type,customer_code,created_at FROM service_orders WHERE service_type='WAREHOUSE'"),
    "orders_confirmed": ("Đơn đang chờ thực hiện", ORDER_READ, False, None, "SELECT code,status,service_type,customer_code,created_at FROM service_orders WHERE status='CONFIRMED'"),
    "orders_in_progress": ("Đơn đang thực hiện", ORDER_READ, False, None, "SELECT code,status,service_type,customer_code,created_at FROM service_orders WHERE status='IN_PROGRESS'"),
    "orders_completed": ("Đơn đã hoàn tất", ORDER_READ, False, None, "SELECT code,status,service_type,customer_code,created_at FROM service_orders WHERE status='COMPLETED'"),
    "orders_completed_period": ("Đơn hoàn tất trong kỳ", ORDER_READ, True, "completed_at", "SELECT o.code,o.status,o.service_type,o.customer_code,a.occurred_at AS completed_at FROM service_orders o JOIN audit_log a ON a.object_type='ServiceOrder' AND a.object_code=o.code AND a.action='ORDER_COMPLETED' WHERE o.status='COMPLETED'"),
    "trips_active": ("Chuyến đang thực hiện", FLEET, False, None, "SELECT code,status,order_code,committed_end FROM transport_trips WHERE status IN ('PICKED_UP','IN_TRANSIT','DELIVERED')"),
    "trips_overdue": ("Chuyến quá hạn", FLEET, False, None, "SELECT code,status,order_code,committed_end FROM transport_trips WHERE status IN ('ASSIGNED','PICKED_UP','IN_TRANSIT') AND committed_end IS NOT NULL AND committed_end < :now_utc"),
    "rentals_active": ("Xe thuê đã bàn giao", FLEET, False, None, "SELECT code,status,order_code,requested_end FROM rental_records WHERE status='HANDED_OVER'"),
    "rentals_overdue": ("Xe thuê quá hạn", FLEET, False, None, "SELECT r.code,r.status,r.order_code,b.planned_end FROM rental_records r JOIN resource_bookings b ON b.id=r.booking_id WHERE r.status='HANDED_OVER' AND b.status='IN_USE' AND b.planned_end < :now_utc"),
    "vehicles_in_use": ("Xe đang sử dụng", FLEET, False, None, "SELECT v.id,v.plate_number,v.status FROM vehicles v WHERE EXISTS (SELECT 1 FROM resource_bookings b WHERE b.vehicle_id=v.id AND b.status='IN_USE')"),
    "vehicles_maintenance": ("Xe bảo dưỡng", FLEET, False, None, "SELECT id,plate_number,status FROM vehicles WHERE status='MAINTENANCE'"),
    "lots_stock": ("Lô còn hàng", WAREHOUSE, False, None, "SELECT l.code,l.order_code,l.status FROM warehouse_lots l WHERE l.status='IN_STORAGE' AND EXISTS (SELECT 1 FROM lot_lines x WHERE x.lot_id=l.id AND x.on_hand>0)"),
    "receivables_balance": ("Tổng còn phải thu (VND)", FINANCE, False, None, "SELECT o.code,o.status,o.customer_code,o.payment_due_date,o.price_snapshot_vnd + COALESCE(c.charges,0) - COALESCE(p.paid,0) AS amount_vnd FROM service_orders o LEFT JOIN (SELECT order_code,SUM(amount_vnd) charges FROM charges WHERE status='APPROVED' GROUP BY order_code) c ON c.order_code=o.code LEFT JOIN (SELECT order_code,SUM(CASE WHEN reversal_of IS NULL THEN amount_vnd ELSE -amount_vnd END) paid FROM payments GROUP BY order_code) p ON p.order_code=o.code WHERE o.status<>'CANCELLED' AND o.price_snapshot_vnd + COALESCE(c.charges,0) - COALESCE(p.paid,0)>0"),
    "receivables_overdue": ("Đơn quá hạn thanh toán", FINANCE, False, None, "SELECT o.code,o.status,o.customer_code,o.payment_due_date FROM service_orders o LEFT JOIN (SELECT order_code,SUM(amount_vnd) charges FROM charges WHERE status='APPROVED' GROUP BY order_code) c ON c.order_code=o.code LEFT JOIN (SELECT order_code,SUM(CASE WHEN reversal_of IS NULL THEN amount_vnd ELSE -amount_vnd END) paid FROM payments GROUP BY order_code) p ON p.order_code=o.code WHERE o.status<>'CANCELLED' AND o.payment_due_date<:today AND o.price_snapshot_vnd + COALESCE(c.charges,0) - COALESCE(p.paid,0)>0"),
    "payments_period": ("Thu ròng trong kỳ (VND)", FINANCE, True, "occurred_at", "SELECT code,order_code,amount_vnd,occurred_at,method,reversal_of,CASE WHEN reversal_of IS NULL THEN amount_vnd ELSE -amount_vnd END AS signed_amount_vnd FROM payments"),
}
AMOUNTS = {"receivables_balance": "amount_vnd", "payments_period": "signed_amount_vnd"}
REPORTS = frozenset({"orders", "schedule", "stock", "receivables"})


def _bounds(start: date | None, end: date | None):
    if start and end and start > end:
        raise ValidationFailed("Ngày bắt đầu phải trước hoặc bằng ngày kết thúc.", details={"field": "start"})
    first = to_db(datetime.combine(start, time.min, TZ)) if start else None
    last = to_db(datetime.combine(end + timedelta(days=1), time.min, TZ)) if end else None
    return first, last


def _query(metric, roles, start, end, now):
    if metric not in METRICS:
        raise ValidationFailed("Chỉ số không tồn tại.", details={"field": "metric"})
    title, allowed, period, column, sql = METRICS[metric]
    if not allowed.intersection(roles):
        raise Forbidden("Không có quyền xem chỉ số này.")
    first, last = _bounds(start, end)
    params = {"now_utc": to_db(now), "today": now.astimezone(TZ).date().isoformat()}
    if period:
        clauses = []
        if first: clauses.append(f"{column} >= :start_utc"); params["start_utc"] = first
        if last: clauses.append(f"{column} < :end_utc"); params["end_utc"] = last
        if clauses: sql = f"SELECT * FROM ({sql}) WHERE " + " AND ".join(clauses)
    return title, period, sql, params


def metric_rows(conn, metric, roles, start, end, now, *, limit=20, offset=0):
    title, period, sql, params = _query(metric, roles, start, end, now)
    aggregate = f"SUM({AMOUNTS[metric]})" if metric in AMOUNTS else "COUNT(*)"
    value = conn.execute(f"SELECT COALESCE({aggregate},0) FROM ({sql})", params).fetchone()[0]
    count = conn.execute(f"SELECT COUNT(*) FROM ({sql})", params).fetchone()[0]
    rows = [dict(row) for row in conn.execute(f"SELECT * FROM ({sql}) ORDER BY 1 DESC LIMIT :limit OFFSET :offset", {**params, "limit": limit, "offset": offset})]
    return {"metric": metric, "title": title, "value": value, "total": count, "period": period, "items": rows}


def dashboard(conn, roles, start, end, now):
    metrics = []
    for key, (_, allowed, _, _, _) in METRICS.items():
        if allowed.intersection(roles):
            item = metric_rows(conn, key, roles, start, end, now, limit=1)
            metrics.append({k: item[k] for k in ("metric", "title", "value", "period")})
    return {"updated_at": to_db(now), "start": start, "end": end, "metrics": metrics}


def csv_safe(value):
    if isinstance(value, (int, float)):
        return value
    text = "" if value is None else str(value)
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@", "\t", "\r", "\n")) else text


def report_query(report, roles, start, end, now):
    if report == "orders":
        return _query("orders_new", roles, start, end, now)[2:]
    if report == "receivables":
        return _query("receivables_balance", roles, start, end, now)[2:]
    _bounds(start, end)
    if report == "schedule" and FLEET.intersection(roles):
        sql = "SELECT b.code,b.source_type,b.source_code,b.vehicle_id,b.driver_id,b.planned_start,b.planned_end,b.status FROM resource_bookings b"
        params = {}
        first, last = _bounds(start, end)
        clauses = []
        if first: clauses.append("b.planned_end>:start_utc"); params["start_utc"] = first
        if last: clauses.append("b.planned_start<:end_utc"); params["end_utc"] = last
        return sql + (" WHERE " + " AND ".join(clauses) if clauses else ""), params
    if report == "stock" and WAREHOUSE.intersection(roles):
        return ("SELECT l.code AS lot_code,l.status,x.item_code,x.unit_code,x.expected_qty,x.on_hand "
                "FROM warehouse_lots l JOIN lot_lines x ON x.lot_id=l.id"), {}
    if report not in REPORTS:
        raise ValidationFailed("Loại báo cáo không tồn tại.", details={"field": "report"})
    raise Forbidden("Không có quyền xuất báo cáo này.")


def export_csv(conn, report, roles, start, end, now):
    sql, params = report_query(report, roles, start, end, now)
    cursor = conn.execute(f"SELECT * FROM ({sql}) ORDER BY 1 DESC", params)
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    columns = [item[0] for item in cursor.description]
    writer.writerow(columns)
    for row in cursor:
        writer.writerow([csv_safe(value) for value in row])
    return output.getvalue()
