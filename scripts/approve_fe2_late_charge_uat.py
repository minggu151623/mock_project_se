"""Approve the pending late-return charge in an isolated FE2 UAT database."""

import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.main import create_app


def approve(path: str, charge_code: str):
    db = Path(path).resolve()
    if db.parent != (ROOT / "data").resolve() or not db.name.startswith("fe2-uat") or not db.is_file():
        raise ValueError("Chỉ dùng DB UAT đã có trong data/ với tên bắt đầu fe2-uat.")
    client = TestClient(create_app(str(db), files_dir=str(ROOT / "data" / f"{db.stem}-files")))
    login = client.post("/api/auth/login", json={"username": "mgr1", "password": "Password@123"})
    assert login.status_code == 200, login.text
    response = client.post(f"/api/sales/charges/{charge_code}/decision",
                           headers={"Authorization": f"Bearer {login.json()['token']}",
                                    "Idempotency-Key": f"uat-late-approve-{charge_code}"},
                           json={"approved": True, "decision_note": "Đã đối chiếu biên bản trả xe quá hạn"})
    assert response.status_code == 200, response.text
    print(json.dumps({"charge": charge_code, "status": response.json()["status"]}, ensure_ascii=False))


if __name__ == "__main__":
    approve(sys.argv[1], sys.argv[2])
