"""Tạo dữ liệu test. Giờ ghi theo giờ Việt Nam cho dễ đọc."""

from datetime import datetime

from backend import assignments, fleet, warehouse
from backend.db import write_tx
from backend.timeutil import SYSTEM_TZ

DSP = {"X-Dev-User": "dsp1", "X-Dev-Roles": "DSP"}
DSP2 = {"X-Dev-User": "dsp2", "X-Dev-Roles": "DSP"}
SAL = {"X-Dev-User": "sal1", "X-Dev-Roles": "SAL"}
ADM = {"X-Dev-User": "adm1", "X-Dev-Roles": "ADM"}
WHS = {"X-Dev-User": "whs1", "X-Dev-Roles": "WHS"}
MGR = {"X-Dev-User": "mgr1", "X-Dev-Roles": "MGR"}
FIN = {"X-Dev-User": "fin1", "X-Dev-Roles": "FIN"}
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PDF = b"%PDF-1.4\n%test\n"
ACTOR = "dsp1"


def vn(hour: int, minute: int = 0, day: int = 1) -> datetime:
    return datetime(2026, 12, day, hour, minute, tzinfo=SYSTEM_TZ)


def iso(hour: int, minute: int = 0, day: int = 1) -> str:
    return vn(hour, minute, day).isoformat()


def vehicle(conn, now, plate="51C-100.01", capacity=2000, vtype="TRUCK_2T", status="ACTIVE") -> dict:
    with write_tx(conn):
        return fleet.create_vehicle(conn, plate_number=plate, vehicle_type=vtype, capacity_kg=capacity,
                                    status=status, actor=ACTOR, now=now)


def driver(conn, now, name="Nguyễn Văn A", phone="0901234567", status="ACTIVE") -> dict:
    with write_tx(conn):
        return fleet.create_driver(conn, full_name=name, phone=phone, status=status, actor=ACTOR, now=now)


def trip(conn, now, order="ORD-T-001", kg=1200.0, customer="KH-001", from_warehouse=False,
         committed_end=None) -> dict:
    with write_tx(conn):
        return assignments.create_transport_trip(
            conn, order_code=order, cargo_weight_kg=kg, customer_code=customer, pickup_address="Kho trung chuyển",
            delivery_address="12 Nguyễn Huệ, Q1", pickup_from_warehouse=from_warehouse,
            committed_end=committed_end, actor=ACTOR, now=now)


def lot(conn, now, order="ORD-W-001", customer="KH-001", lines=None) -> dict:
    lines = lines if lines is not None else [
        {"item_code": "HANG-A", "item_name": "Hàng A", "unit_code": "KIEN", "expected_qty": 100}]
    with write_tx(conn):
        return warehouse.create_warehouse_lot(conn, order_code=order, customer_code=customer, lines=lines,
                                              actor=ACTOR, now=now)


def upload(client, headers=None, name="bang-chung.png", data=PNG):
    r = client.post("/api/attachments", headers=headers or DSP, files={"file": (name, data, "image/png")})
    assert r.status_code == 201, r.text
    return r.json()["code"]


def rental(conn, now, order="ORD-R-001", vtype="TRUCK_2T", min_kg=1000, start=None, end=None) -> dict:
    with write_tx(conn):
        return assignments.create_rental_record(
            conn, order_code=order, vehicle_type=vtype, min_capacity_kg=min_kg,
            requested_start=start or vn(8), requested_end=end or vn(18), actor=ACTOR, now=now)
