"""Nạp dữ liệu mẫu cho dev và FE2 thử giao diện.

Chạy: .venv/bin/python -m backend.seed_dev [đường_dẫn_db]
Dữ liệu theo tài liệu 08 mục 2: 6 xe ở 2 mức tải, có ACTIVE/MAINTENANCE/INACTIVE;
4 tài xế, có một INACTIVE; vài chuyến và hồ sơ thuê chờ phân công; 3 lô kho, có lô
2 dòng khác đơn vị, lô đã nhập nhiều lần và xuất một phần.
"""

import sys
from datetime import datetime, timedelta

from . import assignments, fleet, warehouse
from .db import connect, init_db, write_tx
from .timeutil import SYSTEM_TZ, system_now

ACTOR = "seed"


def seed(db_path: str) -> None:
    conn = connect(db_path)
    init_db(conn)
    if conn.execute("SELECT COUNT(*) FROM vehicles").fetchone()[0]:
        print("DB đã có dữ liệu, bỏ qua.")
        return
    now = system_now()
    tomorrow = (now.astimezone(SYSTEM_TZ) + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)

    def at(hour: int, day: int = 0) -> datetime:
        return tomorrow + timedelta(days=day, hours=hour)

    with write_tx(conn):
        for plate, vtype, kg, status in [
            ("51C-111.01", "TRUCK_2T", 2000, "ACTIVE"),
            ("51C-111.02", "TRUCK_2T", 2000, "ACTIVE"),
            ("51C-111.03", "TRUCK_2T", 2000, "MAINTENANCE"),
            ("51D-222.01", "TRUCK_5T", 5000, "ACTIVE"),
            ("51D-222.02", "TRUCK_5T", 5000, "ACTIVE"),
            ("51D-222.03", "TRUCK_5T", 5000, "INACTIVE"),
        ]:
            fleet.create_vehicle(conn, plate_number=plate, vehicle_type=vtype, capacity_kg=kg, status=status,
                                 actor=ACTOR, now=now)
        for name, phone, status in [
            ("Nguyễn Văn An", "0901000001", "ACTIVE"),
            ("Trần Thị Bình", "0901000002", "ACTIVE"),
            ("Lê Văn Cường", "0901000003", "ACTIVE"),
            ("Phạm Văn Dũng", "0901000004", "INACTIVE"),
        ]:
            fleet.create_driver(conn, full_name=name, phone=phone, status=status, actor=ACTOR, now=now)

        t1 = assignments.create_transport_trip(
            conn, order_code="ORD-DEMO-T01", cargo_weight_kg=1200, customer_code="KH-001",
            pickup_address="25 Lê Lợi, Q1", delivery_address="KCN Tân Bình", committed_end=at(12),
            actor=ACTOR, now=now)
        assignments.create_transport_trip(
            conn, order_code="ORD-DEMO-T02", cargo_weight_kg=3500, customer_code="KH-002",
            pickup_address="Cảng Cát Lái", delivery_address="Biên Hòa", actor=ACTOR, now=now)
        assignments.create_transport_trip(
            conn, order_code="ORD-DEMO-T03", cargo_weight_kg=800, customer_code="KH-003",
            pickup_address="Kho trung chuyển", delivery_address="Thủ Đức", pickup_from_warehouse=True,
            actor=ACTOR, now=now)
        r1 = assignments.create_rental_record(conn, order_code="ORD-DEMO-R01", vehicle_type="TRUCK_2T",
                                              min_capacity_kg=1500, requested_start=at(8), requested_end=at(18),
                                              actor=ACTOR, now=now)
        assignments.create_rental_record(conn, order_code="ORD-DEMO-R02", vehicle_type="TRUCK_5T",
                                         min_capacity_kg=4000, requested_start=at(7, 1), requested_end=at(17, 2),
                                         actor=ACTOR, now=now)

        # Một chuyến đã phân công và một xe đã giữ cho thuê, để lịch có sẵn dữ liệu.
        assignments.assign_trip(conn, t1["code"], vehicle_id=1, driver_id=1, planned_start=at(8),
                                planned_end=at(11), actor=ACTOR, now=now)
        assignments.reserve_rental(conn, r1["code"], vehicle_id=2, actor=ACTOR, now=now)

        # Kho: một lô chờ nhận, một lô 2 dòng khác đơn vị đã nhập 2 lần và xuất một phần.
        warehouse.create_warehouse_lot(
            conn, order_code="ORD-DEMO-W01", customer_code="KH-001", actor=ACTOR, now=now,
            lines=[{"item_code": "THUNG-NUOC", "item_name": "Nước suối thùng 24 chai", "unit_code": "THUNG",
                    "expected_qty": 200}])
        lot = warehouse.create_warehouse_lot(
            conn, order_code="ORD-DEMO-W02", customer_code="KH-003", actor=ACTOR, now=now,
            storage_notes="Tránh mưa, xếp không quá 3 tầng",
            lines=[{"item_code": "KIEN-MAY", "item_name": "Kiện linh kiện máy", "unit_code": "KIEN",
                    "expected_qty": 100},
                   {"item_code": "PALLET-SAT", "item_name": "Pallet thép tấm", "unit_code": "PALLET",
                    "expected_qty": 6}])
        kien, pallet = (line["id"] for line in lot["lines"])
        for qty in (60, 40):
            doc = warehouse.create_receipt(conn, lot["code"], occurred_at=now, counterpart_name="Tài xế KH-003",
                                           lines=[{"lot_line_id": kien, "quantity": qty},
                                                  {"lot_line_id": pallet, "quantity": 3}],
                                           discrepancy_reason="", actor=ACTOR, now=now)
            warehouse.post_document(conn, doc["code"], actor=ACTOR, now=now)
        out = warehouse.create_issue(conn, lot["code"], occurred_at=now, counterpart_name="Tài xế xe 51D-222.01",
                                     destination="Thủ Đức", lines=[{"lot_line_id": kien, "quantity": 30}],
                                     actor=ACTOR, now=now)
        warehouse.post_document(conn, out["code"], actor=ACTOR, now=now)
        warehouse.create_warehouse_lot(
            conn, order_code="ORD-DEMO-W03", customer_code="KH-002", actor=ACTOR, now=now,
            lines=[{"item_code": "BAO-GAO", "item_name": "Bao gạo 50 kg", "unit_code": "BAO", "expected_qty": 40}])
    conn.close()
    print(f"Đã nạp dữ liệu mẫu vào {db_path}")


if __name__ == "__main__":
    seed(sys.argv[1] if len(sys.argv) > 1 else "data/dev.sqlite3")
