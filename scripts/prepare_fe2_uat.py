"""Create reproducible FE2 UAT data in an isolated, clearly named local database."""

import json
import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend import admin, sales, seed_be3_dev, warehouse
from backend.db import connect, write_tx
from backend.timeutil import SYSTEM_TZ, system_now

DEFAULT = ROOT / "data" / "fe2-uat.sqlite3"


def prepare(path=DEFAULT):
    path = Path(path).resolve()
    if path.parent != (ROOT / "data").resolve() or not path.name.startswith("fe2-uat"):
        raise ValueError("Chỉ tạo DB UAT trong data/ với tên bắt đầu fe2-uat; không ghi lên DB dev.")
    path.parent.mkdir(parents=True, exist_ok=True)
    seed_be3_dev.seed(str(path))
    conn = connect(str(path))
    now = system_now()
    today = now.astimezone(SYSTEM_TZ).date()
    try:
        with write_tx(conn):
            if not conn.execute("SELECT 1 FROM users WHERE username='uat_multi'").fetchone():
                admin.create_user(conn, username="uat_multi", full_name="Nhân viên UAT kiêm nhiệm",
                                  password=seed_be3_dev.DEFAULT_PASSWORD, roles=["DSP", "WHS"],
                                  status="ACTIVE", must_change_password=False,
                                  actor="seed-fe2-uat", now=now)
            for i in range(2, 6):
                phone = f"09093300{i:02d}"
                if not conn.execute("SELECT 1 FROM customers WHERE phone=?", (phone,)).fetchone():
                    sales.create_customer(conn, customer_type="BUSINESS" if i % 2 else "INDIVIDUAL",
                                          name=f"Khách UAT {i}", contact_name=f"Người liên hệ UAT {i}",
                                          phone=phone, email=f"uat{i}@example.test", address="Địa chỉ thử nghiệm",
                                          actor="seed-fe2-uat", now=now)
            specs = [("TRANSPORT", 1), ("RENTAL", 2), ("WAREHOUSE", 2)]
            customers = [dict(row) for row in conn.execute("SELECT * FROM customers ORDER BY id")]
            for service, target_add in specs:
                existing = conn.execute("SELECT COUNT(*) FROM service_orders WHERE service_type=?", (service,)).fetchone()[0]
                target = {"TRANSPORT": 3, "RENTAL": 3, "WAREHOUSE": 3}[service]
                sample = conn.execute("SELECT r.service_data_json FROM service_requests r WHERE r.service_type=? AND r.status='CONFIRMED' LIMIT 1", (service,)).fetchone()
                if sample is None:
                    raise RuntimeError(f"Thiếu yêu cầu mẫu {service}.")
                service_data = json.loads(sample[0])
                while existing < target:
                    customer = customers[(existing + len(service)) % len(customers)]
                    request = sales.create_request(conn, service_type=service, source="INTERNAL",
                        contact={"name": customer["name"], "contact_name": customer["contact_name"],
                                 "phone": customer["phone"], "email": customer["email"]},
                        service_data=service_data, actor="sal1", now=now)
                    sales.update_request(conn, request["code"], version=request["version"],
                                         customer_code=customer["code"], actor="sal1", now=now)
                    quote = sales.create_quote(conn, request_code=request["code"],
                        valid_until=(today + timedelta(days=14)).isoformat(), service_data=service_data,
                        terms="Dữ liệu UAT", lines=[{"description": "Phí dịch vụ UAT", "quantity": 1,
                        "unit_price_vnd": 1_000_000 + existing * 100_000}], actor="sal1", now=now)
                    sales.issue_quote(conn, quote["code"], actor="sal1", now=now)
                    sales.respond_quote_and_create_order(conn, quote["code"], accepted=True,
                        channel="PHONE", content="Khách mẫu xác nhận", evidence=[],
                        payment_due_date=today + timedelta(days=7), actor="sal1", now=now)
                    existing += 1
            while conn.execute("SELECT COUNT(*) FROM service_requests").fetchone()[0] < 12:
                customer = customers[0]
                sample = conn.execute("SELECT service_data_json FROM service_requests WHERE service_type='TRANSPORT' LIMIT 1").fetchone()
                sales.create_request(conn, service_type="TRANSPORT", source="INTERNAL",
                    contact={"name": customer["name"], "contact_name": customer["contact_name"],
                             "phone": customer["phone"], "email": customer["email"]},
                    service_data=json.loads(sample[0]), actor="sal1", now=now)
            rental_order = conn.execute("SELECT code,price_snapshot_vnd FROM service_orders WHERE service_type='RENTAL' ORDER BY id DESC LIMIT 1").fetchone()
            if not conn.execute("SELECT 1 FROM payments WHERE order_code=?", (rental_order["code"],)).fetchone():
                sales.record_payment(conn, rental_order["code"], amount_vnd=rental_order["price_snapshot_vnd"],
                    occurred_at=now, method="BANK_TRANSFER", reference="UAT-PAID-001", note="Đã thu đủ UAT",
                    actor="fin1", now=now)
            warehouse_order = conn.execute("SELECT code,price_snapshot_vnd FROM service_orders WHERE service_type='WAREHOUSE' ORDER BY id DESC LIMIT 1").fetchone()
            if not conn.execute("SELECT 1 FROM payments WHERE order_code=?", (warehouse_order["code"],)).fetchone():
                payment = sales.record_payment(conn, warehouse_order["code"], amount_vnd=100_000,
                    occurred_at=now, method="CASH", reference=None, note="Thu nhầm để thử đảo",
                    actor="fin1", now=now)["payment"]
                sales.reverse_payment(conn, payment["code"], reason="Nhập nhầm khoản thu UAT", actor="fin1", now=now)
            overdue = conn.execute("SELECT code,version,payment_due_date FROM service_orders WHERE service_type='TRANSPORT' ORDER BY id DESC LIMIT 1").fetchone()
            if overdue["payment_due_date"] >= today.isoformat():
                sales.update_due_date(conn, overdue["code"], due_date=today - timedelta(days=1),
                    reason="Tình huống quá hạn UAT", version=overdue["version"], actor="sal1", now=now)
            lot_row = conn.execute("SELECT code FROM warehouse_lots WHERE order_code=?", (warehouse_order["code"],)).fetchone()
            lot = warehouse.get_lot(conn, lot_row["code"], now)
            if lot["status"] == "WAITING_RECEIPT":
                lines = [{"lot_line_id": line["id"], "quantity": line["expected_qty"]} for line in lot["lines"]]
                receipt = warehouse.create_receipt(conn, lot["code"], occurred_at=now,
                    counterpart_name="Người giao UAT", lines=lines, actor="whs1", now=now)
                warehouse.post_document(conn, receipt["code"], actor="whs1", now=now)
                issue = warehouse.create_issue(conn, lot["code"], occurred_at=now,
                    counterpart_name="Người nhận UAT", destination="Điểm nhận UAT", lines=lines,
                    actor="whs1", now=now)
                warehouse.post_document(conn, issue["code"], actor="whs1", now=now)
                lot = warehouse.get_lot(conn, lot["code"], now)
                warehouse.close_lot(conn, lot["code"], expected_version=lot["version"],
                    no_more_receipts=True, actor="whs1", now=now)
            linked_trip = conn.execute(
                "SELECT t.code FROM transport_trips t JOIN service_orders to_order ON to_order.code=t.order_code "
                "JOIN service_orders wh_order ON wh_order.customer_code=to_order.customer_code "
                "JOIN warehouse_lots l ON l.order_code=wh_order.code "
                "WHERE l.status='WAITING_RECEIPT' AND t.status='UNASSIGNED' "
                "ORDER BY l.id,t.id LIMIT 1"
            ).fetchone()
            if linked_trip:
                conn.execute("UPDATE transport_trips SET pickup_from_warehouse=1 WHERE code=?",
                             (linked_trip["code"],))
        counts = {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                  for table in ("users", "customers", "vehicles", "drivers", "service_requests",
                                "service_orders", "transport_trips", "rental_records", "warehouse_lots")}
        counts["orders_by_service"] = {row[0]: row[1] for row in conn.execute(
            "SELECT service_type,COUNT(*) FROM service_orders GROUP BY service_type")}
        scenarios = {
            "transport_assigned": conn.execute("SELECT code FROM transport_trips WHERE status='ASSIGNED' ORDER BY id LIMIT 1").fetchone()[0],
            "rental_reserved": conn.execute("SELECT code FROM rental_records WHERE status='RESERVED' ORDER BY id LIMIT 1").fetchone()[0],
            "warehouse_completed_zero_stock": lot_row["code"],
            "order_paid": rental_order["code"],
            "order_payment_reversed": warehouse_order["code"],
            "order_overdue": overdue["code"],
            "warehouse_linkable_trip": linked_trip["code"] if linked_trip else None,
        }
        manifest = path.with_suffix(".json")
        manifest.write_text(json.dumps({"database": str(path), "counts": counts, "scenarios": scenarios}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(counts, ensure_ascii=False))
        print(f"Manifest: {manifest}")
    finally:
        conn.close()


if __name__ == "__main__":
    prepare(sys.argv[1] if len(sys.argv) > 1 else DEFAULT)
