"""Measure FE2 dashboard endpoints on the NFR-04 sized dataset.

Run outside a restricted Windows socket sandbox because FastAPI TestClient
needs a local socket pair for its event loop.
"""

import json
import tempfile
import time
from pathlib import Path

from fastapi.testclient import TestClient

from backend.main import create_app
from scripts.benchmark_nfr04 import (ORDERS_TARGET, WAREHOUSE_TX_TARGET, _stats_ms,
                                     populate_benchmark_db)

SAMPLES = 10
READ_P95_MS = 2000


def run_benchmark():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
        db_path = str(Path(directory) / "fe2-dashboard.sqlite3")
        seed = populate_benchmark_db(db_path)
        client = TestClient(create_app(db_path, files_dir=str(Path(directory) / "files")))
        results = {}
        cases = [
            ("fin1", "/api/fe2/dashboard"),
            ("fin1", "/api/fe2/dashboard/receivables_balance"),
            ("fin1", "/api/fe2/dashboard/receivables_overdue"),
            ("sal1", "/api/fe2/dashboard"),
            ("whs1", "/api/fe2/dashboard/lots_stock"),
        ]
        tokens = {}
        for username, _ in cases:
            if username in tokens:
                continue
            response = client.post("/api/auth/login", json={"username": username, "password": "Password@123"})
            assert response.status_code == 200, response.text
            tokens[username] = response.json()["token"]
        for username, path in cases:
            durations = []
            for _ in range(SAMPLES):
                started = time.perf_counter()
                response = client.get(path, headers={"Authorization": f"Bearer {tokens[username]}"})
                durations.append(time.perf_counter() - started)
                assert response.status_code == 200, response.text
            results[f"{username} GET {path}"] = _stats_ms(durations)
        summary = {
            "orders_seeded": ORDERS_TARGET,
            "warehouse_transactions_seeded": WAREHOUSE_TX_TARGET,
            "seed_seconds": seed["seed_seconds"],
            "samples_per_endpoint": SAMPLES,
            "read_p95_limit_ms": READ_P95_MS,
            "results": results,
        }
        summary["passed"] = all(item["p95_ms"] <= READ_P95_MS for item in results.values())
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
        return summary


if __name__ == "__main__":
    if not run_benchmark()["passed"]:
        raise SystemExit(1)
