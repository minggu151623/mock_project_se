"""Prepare an overdue, handed-over rental for FE2 UI acceptance testing.

Only accepts an isolated data/fe2-uat*.sqlite3 database. Re-running is safe.
"""

import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.main import create_app
from backend.timeutil import system_now
from tests.factories import PNG


def prepare(path: str, code: str = "RNT-000004", reference_at: str | None = None):
    db = Path(path).resolve()
    if db.parent != (ROOT / "data").resolve() or not db.name.startswith("fe2-uat") or not db.is_file():
        raise ValueError("Chỉ dùng DB UAT đã có trong data/ với tên bắt đầu fe2-uat.")
    real_now = datetime.fromisoformat(reference_at.replace("Z", "+00:00")) if reference_at else system_now()
    if real_now.tzinfo is None:
        raise ValueError("reference_at cần có múi giờ.")
    start = real_now - timedelta(hours=3)
    end = real_now - timedelta(hours=2)
    clock_value = [start - timedelta(minutes=1)]
    files_dir = ROOT / "data" / f"{db.stem}-files"
    client = TestClient(create_app(str(db), files_dir=str(files_dir), clock=lambda: clock_value[0]))
    login = client.post("/api/auth/login", json={"username": "dsp1", "password": "Password@123"})
    assert login.status_code == 200, login.text
    headers = {"Authorization": f"Bearer {login.json()['token']}"}
    current = client.get(f"/api/rentals/{code}", headers=headers)
    assert current.status_code == 200, current.text
    if current.json()["status"] not in {"UNASSIGNED", "RESERVED"}:
        print(json.dumps({"rental": code, "status": current.json()["status"], "already_prepared": True}))
        return
    vehicles = client.get("/api/availability/vehicles", headers=headers,
                          params={"start": start.isoformat(), "end": end.isoformat(),
                                  "vehicle_type": current.json()["vehicle_type"],
                                  "min_capacity_kg": current.json()["min_capacity_kg"]}).json()["items"]
    with sqlite3.connect(db) as conn:
        in_use_ids = {row[0] for row in conn.execute(
            "SELECT vehicle_id FROM resource_bookings WHERE status = 'IN_USE' AND vehicle_id IS NOT NULL"
        )}
    candidates = [vehicle for vehicle in vehicles if vehicle["id"] not in in_use_ids]
    if not candidates:
        raise RuntimeError("Không có xe trống để tạo tình huống trả muộn UAT.")
    if current.json()["status"] == "UNASSIGNED":
        reserve = client.post(f"/api/rentals/{code}/reservation", headers={**headers, "Idempotency-Key": f"uat-late-reserve-{code}"},
                              json={"vehicle_id": candidates[0]["id"], "planned_start": start.isoformat(),
                                    "planned_end": end.isoformat()})
        assert reserve.status_code == 201, reserve.text
    else:
        change = client.put(f"/api/rentals/{code}/reservation", headers=headers,
                            json={"version": current.json()["version"], "vehicle_id": candidates[0]["id"],
                                  "planned_start": start.isoformat(), "planned_end": end.isoformat(),
                                  "reason": "Chuẩn bị UAT trả muộn", "customer_note": "Đổi xe kiểm thử"})
        assert change.status_code == 200, change.text
    clock_value[0] = start + timedelta(minutes=1)
    evidence = client.post("/api/attachments", headers=headers,
                           files={"file": ("uat-late-handover.png", PNG, "image/png")})
    assert evidence.status_code == 201, evidence.text
    handover = client.post(f"/api/rentals/{code}/handover",
                           headers={**headers, "Idempotency-Key": f"uat-late-handover-{code}"},
                           json={"actual_at": clock_value[0].isoformat(), "receiver_name": "Khách UAT",
                                 "odometer_km": 500, "body_condition": "Nguyên vẹn", "fuel_level": "3/4 bình",
                                 "accessories": "Kích, lốp dự phòng",
                                 "attachment_codes": [evidence.json()["code"]]})
    assert handover.status_code == 200, handover.text
    print(json.dumps({"rental": code, "status": handover.json()["status"],
                      "planned_end": end.isoformat(), "overdue_minutes_at_prepare":
                      int((real_now - end).total_seconds() // 60)}, ensure_ascii=False))


if __name__ == "__main__":
    prepare(
        sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "data" / "fe2-uat.sqlite3"),
        sys.argv[2] if len(sys.argv) > 2 else "RNT-000004",
        sys.argv[3] if len(sys.argv) > 3 else None,
    )
