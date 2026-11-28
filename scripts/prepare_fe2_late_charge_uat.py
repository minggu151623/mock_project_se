"""Propose a late-return charge on an isolated FE2 UAT rental and verify the completion gate."""

import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.main import create_app
from tests.factories import PNG


def prepare(path: str, rental_code: str = "RNT-000003"):
    db = Path(path).resolve()
    if db.parent != (ROOT / "data").resolve() or not db.name.startswith("fe2-uat") or not db.is_file():
        raise ValueError("Chỉ dùng DB UAT đã có trong data/ với tên bắt đầu fe2-uat.")
    client = TestClient(create_app(str(db), files_dir=str(ROOT / "data" / f"{db.stem}-files")))
    login = client.post("/api/auth/login", json={"username": "dsp1", "password": "Password@123"})
    assert login.status_code == 200, login.text
    headers = {"Authorization": f"Bearer {login.json()['token']}"}
    rental = client.get(f"/api/rentals/{rental_code}", headers=headers)
    assert rental.status_code == 200, rental.text
    assert rental.json()["status"] == "RETURNED", rental.text
    order_code = rental.json()["order_code"]
    summary = client.get(f"/api/fe2/rentals/{rental_code}/commercial-summary", headers=headers)
    assert summary.status_code == 200, summary.text
    pending = [c for c in summary.json()["charges"] if c["status"] == "PENDING"]
    if pending:
        charge_code = pending[0]["code"]
    else:
        attachment = client.post("/api/attachments", headers=headers,
                                 files={"file": ("late-return.png", PNG, "image/png")})
        assert attachment.status_code == 201, attachment.text
        charge = client.post(f"/api/sales/orders/{order_code}/charges",
                             headers={**headers, "Idempotency-Key": f"uat-late-charge-{rental_code}"},
                             json={"amount_vnd": 300_000, "reason": "Trả xe quá hạn", "evidence": [attachment.json()["code"]]})
        assert charge.status_code == 201, charge.text
        charge_code = charge.json()["code"]
    blocked = client.post(f"/api/rentals/{rental_code}/complete",
                          headers={**headers, "Idempotency-Key": f"uat-late-complete-before-approval-{rental_code}"})
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "PENDING_CHARGES", blocked.text
    print(json.dumps({"rental": rental_code, "order": order_code, "charge": charge_code,
                      "completion_blocked": blocked.json()["error"]["code"]}, ensure_ascii=False))


if __name__ == "__main__":
    prepare(sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "data" / "fe2-uat.sqlite3"),
            sys.argv[2] if len(sys.argv) > 2 else "RNT-000003")
