"""Đo hiệu năng NFR-04 trên tập dữ liệu mục tiêu:
- 100.000 đơn dịch vụ (`service_orders`, kèm `service_requests` và `quotes`)
- 1.000.000 giao dịch dòng chứng từ kho (`document_lines` gắn với `warehouse_documents`)
- Ngưỡng chấp nhận NFR-04:
  * Thao tác đọc (Read): p95 <= 2.000 ms (2s)
  * Thao tác ghi (Write): p95 <= 3.000 ms (3s)

Chạy:
    .venv/Scripts/python.exe -m scripts.benchmark_nfr04
"""

import hashlib
import json
import math
import statistics
import tempfile
import time
from pathlib import Path

from fastapi.testclient import TestClient

from backend import admin, warehouse
from backend.db import connect, init_db, write_tx
from backend.main import create_app
from backend.timeutil import system_now, to_db

ORDERS_TARGET = 100_000
WAREHOUSE_TX_TARGET = 1_000_000
SAMPLE_RUNS = 30


def _percentile(sorted_values: list[float], p: float) -> float:
    if not sorted_values:
        return 0.0
    k = (len(sorted_values) - 1) * p
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_values[int(k)]
    return sorted_values[f] * (c - k) + sorted_values[c] * (k - f)


def _stats_ms(durations_sec: list[float]) -> dict[str, float]:
    ms = sorted(d * 1000.0 for d in durations_sec)
    return {
        "count": len(ms),
        "p50_ms": round(_percentile(ms, 0.50), 2),
        "p95_ms": round(_percentile(ms, 0.95), 2),
        "p99_ms": round(_percentile(ms, 0.99), 2),
        "max_ms": round(ms[-1], 2) if ms else 0.0,
        "mean_ms": round(statistics.mean(ms), 2) if ms else 0.0,
    }


def populate_benchmark_db(db_path: str) -> dict:
    t0 = time.perf_counter()
    conn = connect(db_path)
    init_db(conn)
    now = system_now()
    now_s = to_db(now)

    # Tối ưu tốc độ nạp batch 100k đơn + 1M giao dịch kho bằng SQLite bulk transaction.
    conn.execute("PRAGMA synchronous = OFF")
    conn.execute("PRAGMA temp_store = MEMORY")
    conn.execute("PRAGMA cache_size = -128000")

    sample_token = "benchmark-public-lookup-token-128bit-0001"
    sample_token_hash = hashlib.sha256(sample_token.encode("utf-8")).hexdigest()

    conn.execute("BEGIN")
    conn.execute(
        "INSERT INTO customers(id, code, customer_type, name, contact_name, phone, email, address, "
        "status, version, created_at, created_by, updated_at, updated_by) "
        "VALUES (1, 'CUS-BENCH01', 'BUSINESS', 'Công ty Benchmark', 'Nguyễn Benchmark', "
        "'0909000999', 'bench@example.test', 'TP. HCM', 'ACTIVE', 1, ?, 'bench', ?, 'bench')",
        (now_s, now_s),
    )

    # 1. Nạp 100.000 service_requests, quotes, service_orders bằng recursive CTE trong C-engine của SQLite.
    conn.execute(
        """
        WITH RECURSIVE seq(n) AS (
            SELECT 1
            UNION ALL
            SELECT n + 1 FROM seq WHERE n < ?
        )
        INSERT INTO service_requests(
            id, code, service_type, source, customer_code, initial_contact_json,
            service_data_json, status, assigned_to, public_token_hash, version,
            created_at, created_by, updated_at, updated_by
        )
        SELECT
            n,
            printf('REQ-%06d', n),
            CASE n % 3 WHEN 0 THEN 'TRANSPORT' WHEN 1 THEN 'RENTAL' ELSE 'WAREHOUSE' END,
            'WEBSITE',
            'CUS-BENCH01',
            '{"name":"Công ty Benchmark","contact_name":"Nguyễn Benchmark","phone":"0909000999"}',
            '{"cargo_description":"Hàng benchmark"}',
            'CONFIRMED',
            'sal1',
            CASE WHEN n = 49998 THEN ? ELSE NULL END,
            1,
            ?, 'bench', ?, 'bench'
        FROM seq
        """,
        (ORDERS_TARGET, sample_token_hash, now_s, now_s),
    )

    conn.execute(
        """
        WITH RECURSIVE seq(n) AS (
            SELECT 1
            UNION ALL
            SELECT n + 1 FROM seq WHERE n < ?
        )
        INSERT INTO quotes(
            id, code, request_code, version_no, valid_until, service_data_json,
            terms, total_vnd, status, row_version, created_at, created_by, updated_at, updated_by
        )
        SELECT
            n,
            printf('QUO-%06d', n),
            printf('REQ-%06d', n),
            1,
            '2026-12-31',
            '{"cargo_description":"Hàng benchmark"}',
            '',
            1000000 + (n % 500) * 1000,
            'ACCEPTED',
            1,
            ?, 'bench', ?, 'bench'
        FROM seq
        """,
        (ORDERS_TARGET, now_s, now_s),
    )

    conn.execute(
        """
        WITH RECURSIVE seq(n) AS (
            SELECT 1
            UNION ALL
            SELECT n + 1 FROM seq WHERE n < ?
        )
        INSERT INTO service_orders(
            id, code, request_code, quote_code, customer_code, service_type,
            service_snapshot_json, customer_snapshot_json, price_snapshot_vnd,
            operational_code, status, payment_due_date, version,
            created_at, created_by, updated_at, updated_by, contact_note
        )
        SELECT
            n,
            printf('ORD-%06d', n),
            printf('REQ-%06d', n),
            printf('QUO-%06d', n),
            'CUS-BENCH01',
            CASE n % 3 WHEN 0 THEN 'TRANSPORT' WHEN 1 THEN 'RENTAL' ELSE 'WAREHOUSE' END,
            '{"cargo_description":"Hàng benchmark"}',
            '{"code":"CUS-BENCH01","name":"Công ty Benchmark"}',
            1000000 + (n % 500) * 1000,
            printf('OP-%06d', n),
            CASE WHEN n % 10 = 0 THEN 'COMPLETED' ELSE 'CONFIRMED' END,
            CASE WHEN n % 20 = 0 THEN '2026-11-01' ELSE '2026-12-15' END,
            1,
            ?, 'bench', ?, 'bench', ''
        FROM seq
        """,
        (ORDERS_TARGET, now_s, now_s),
    )

    # Tạo bản ghi chuyến cho đơn 49998 (49998 % 3 == 0 -> TRANSPORT) để tra cứu chi tiết và public lookup.
    conn.execute(
        "INSERT INTO transport_trips(id, code, order_code, customer_code, cargo_description, "
        "cargo_weight_kg, pickup_address, delivery_address, pickup_from_warehouse, status, "
        "version, created_at, created_by, updated_at, updated_by) "
        "VALUES (1, 'OP-049998', 'ORD-049998', 'CUS-BENCH01', 'Hàng mẫu', 1000, 'Kho A', 'Kho B', "
        "0, 'IN_TRANSIT', 1, ?, 'bench', ?, 'bench')",
        (now_s, now_s),
    )
    conn.execute(
        "INSERT INTO trip_milestones(trip_code, milestone, actual_at, recorded_at, recorded_by) "
        "VALUES ('OP-049998', 'PICKED_UP', ?, ?, 'dsp1')",
        (now_s, now_s),
    )

    # 2. Nạp 1.000.000 giao dịch kho (warehouse_documents + document_lines) phân bổ trên 10.000 lô kho
    # (mỗi lô 100 giao dịch = 1.000.000 giao dịch kho thực tế).
    conn.execute("DROP TRIGGER IF EXISTS trg_doc_line_insert_final")

    conn.execute(
        """
        WITH RECURSIVE seq(n) AS (
            SELECT 1
            UNION ALL
            SELECT n + 1 FROM seq WHERE n < 10000
        )
        INSERT INTO warehouse_lots(
            id, code, order_code, customer_code, status, version,
            created_at, created_by, updated_at, updated_by
        )
        SELECT
            n,
            printf('LOT-%06d', n),
            printf('ORD-WH-%06d', n),
            'CUS-BENCH01',
            'IN_STORAGE',
            1,
            ?, 'bench', ?, 'bench'
        FROM seq
        """,
        (now_s, now_s),
    )

    conn.execute(
        """
        WITH RECURSIVE seq(n) AS (
            SELECT 1
            UNION ALL
            SELECT n + 1 FROM seq WHERE n < 10000
        )
        INSERT INTO lot_lines(
            id, lot_id, item_code, item_name, unit_code, expected_qty, on_hand,
            version, created_at, created_by, updated_at, updated_by
        )
        SELECT
            n,
            n,
            'ITEM-BENCH',
            'Hàng kho benchmark',
            'KIEN',
            1000,
            100,
            1,
            ?, 'bench', ?, 'bench'
        FROM seq
        """,
        (now_s, now_s),
    )

    conn.execute(
        """
        WITH RECURSIVE seq(n) AS (
            SELECT 1
            UNION ALL
            SELECT n + 1 FROM seq WHERE n < ?
        )
        INSERT INTO warehouse_documents(
            id, code, lot_id, doc_type, status, occurred_at, counterpart_name,
            posted_at, posted_by, version, created_at, created_by, updated_at, updated_by
        )
        SELECT
            n,
            printf('WHD-%07d', n),
            ((n - 1) % 10000) + 1,
            'RECEIPT',
            'POSTED',
            ?, 'NCC Benchmark', ?, 'whs1', 1, ?, 'whs1', ?, 'whs1'
        FROM seq
        """,
        (WAREHOUSE_TX_TARGET, now_s, now_s, now_s, now_s),
    )

    conn.execute(
        """
        WITH RECURSIVE seq(n) AS (
            SELECT 1
            UNION ALL
            SELECT n + 1 FROM seq WHERE n < ?
        )
        INSERT INTO document_lines(id, document_id, lot_line_id, quantity, adjusted_qty)
        SELECT n, n, ((n - 1) % 10000) + 1, 1, 0 FROM seq
        """,
        (WAREHOUSE_TX_TARGET,),
    )

    # Khôi phục trigger bảo vệ chứng từ kho và cập nhật code_sequences.
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_doc_line_insert_final
        BEFORE INSERT ON document_lines
        WHEN (SELECT status FROM warehouse_documents WHERE id = NEW.document_id) <> 'DRAFT'
        BEGIN
            SELECT RAISE(ABORT, 'WAREHOUSE_DOCUMENT_FINAL');
        END
        """
    )
    conn.execute(
        "INSERT OR REPLACE INTO code_sequences(prefix, last_value) VALUES "
        "('REQ', ?), ('QUO', ?), ('ORD', ?), ('WHD', ?)",
        (ORDERS_TARGET, ORDERS_TARGET, ORDERS_TARGET, WAREHOUSE_TX_TARGET),
    )
    conn.execute("COMMIT")

    # Tạo tài khoản thật để đo qua HTTP API với phiên đăng nhập thật.
    with write_tx(conn):
        for uname, role in [("sal1", "SAL"), ("fin1", "FIN"), ("whs1", "WHS"), ("adm1", "ADM")]:
            admin.create_user(
                conn,
                username=uname,
                full_name=f"User {uname}",
                password="Password@123",
                roles=[role],
                must_change_password=False,
                actor="bench",
                now=now,
            )

    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("ANALYZE")
    conn.close()
    seed_seconds = round(time.perf_counter() - t0, 2)
    return {"seed_seconds": seed_seconds, "sample_token": sample_token}


def run_benchmark() -> dict:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        db_path = str(Path(tmp_dir) / "nfr04_bench.sqlite3")
        meta = populate_benchmark_db(db_path)

        app = create_app(db_path, files_dir=str(Path(tmp_dir) / "files"))
        client = TestClient(app)

        tokens = {}
        for uname in ("sal1", "fin1", "whs1", "adm1"):
            r = client.post("/api/auth/login", json={"username": uname, "password": "Password@123"})
            assert r.status_code == 200
            tokens[uname] = {"Authorization": f"Bearer {r.json()['token']}"}

        results: dict[str, dict] = {}

        # 1. Đọc danh sách đơn + tổng hợp công nợ trên 100.000 đơn (lọc theo status + service_type).
        d_list_orders = []
        for i in range(SAMPLE_RUNS):
            t = time.perf_counter()
            r = client.get(
                "/api/sales/orders",
                headers=tokens["fin1"],
                params={"service_type": "TRANSPORT", "status": "CONFIRMED", "limit": 50, "offset": i * 50},
            )
            assert r.status_code == 200
            d_list_orders.append(time.perf_counter() - t)
        results["READ: GET /api/sales/orders (100k orders filtered)"] = _stats_ms(d_list_orders)

        # 2. Đọc chi tiết 1 đơn hàng & số dư công nợ trên DB 100.000 đơn.
        d_order_detail = []
        for _ in range(SAMPLE_RUNS):
            t = time.perf_counter()
            r = client.get("/api/sales/orders/ORD-049998", headers=tokens["fin1"])
            assert r.status_code == 200
            d_order_detail.append(time.perf_counter() - t)
        results["READ: GET /api/sales/orders/{code} (detail & balance)"] = _stats_ms(d_order_detail)

        # 3. Tra cứu tiến độ công khai bằng POST /api/public/lookup trên DB 100.000 yêu cầu.
        d_public_lookup = []
        for _ in range(SAMPLE_RUNS):
            t = time.perf_counter()
            r = client.post(
                "/api/public/lookup",
                json={"request_code": "REQ-049998", "token": meta["sample_token"]},
            )
            assert r.status_code == 200
            d_public_lookup.append(time.perf_counter() - t)
        results["READ: POST /api/public/lookup (100k requests)"] = _stats_ms(d_public_lookup)

        # 4. Đọc tồn kho & thông tin lô trên DB 1.000.000 giao dịch kho.
        d_lot_read = []
        for i in range(SAMPLE_RUNS):
            lot_code = f"LOT-{(i % 10000) + 1:06d}"
            t = time.perf_counter()
            r = client.get(f"/api/warehouse/lots/{lot_code}", headers=tokens["whs1"])
            assert r.status_code == 200
            d_lot_read.append(time.perf_counter() - t)
        results["READ: GET /api/warehouse/lots/{code} (1M warehouse tx)"] = _stats_ms(d_lot_read)

        # 5. Ghi yêu cầu dịch vụ mới từ website công khai (PUB-02/03).
        d_pub_submit = []
        for i in range(SAMPLE_RUNS):
            t = time.perf_counter()
            r = client.post(
                "/api/public/requests",
                headers={"X-Forwarded-For": f"10.20.{i // 4}.{i % 250 + 1}"},
                json={
                    "service_type": "TRANSPORT",
                    "contact": {"name": "KH Bench", "contact_name": "Bench", "phone": "0901234567"},
                    "service_data": {
                        "pickup_address": "Kho A",
                        "delivery_address": "Kho B",
                        "cargo_description": "Hàng",
                        "cargo_weight_kg": 500,
                        "planned_pickup_at": "2026-12-05T08:00:00+07:00",
                    },
                },
            )
            assert r.status_code == 201
            d_pub_submit.append(time.perf_counter() - t)
        results["WRITE: POST /api/public/requests (new website request)"] = _stats_ms(d_pub_submit)

        now_iso = system_now().isoformat()

        # 6. Ghi nhận thanh toán trên các đơn trong DB 100.000 đơn.
        d_payment_write = []
        for i in range(1, SAMPLE_RUNS + 1):
            order_code = f"ORD-{i:06d}"
            t = time.perf_counter()
            r = client.post(
                f"/api/sales/orders/{order_code}/payments",
                headers=tokens["fin1"],
                json={
                    "amount_vnd": 100_000,
                    "occurred_at": now_iso,
                    "method": "CASH",
                },
            )
            assert r.status_code == 201, r.text
            d_payment_write.append(time.perf_counter() - t)
        results["WRITE: POST /api/sales/orders/{code}/payments (100k DB)"] = _stats_ms(d_payment_write)

        # 7. Lập và xác nhận phiếu nhập kho trên DB 1.000.000 giao dịch kho.
        d_wh_write = []
        for i in range(SAMPLE_RUNS):
            lot_id = (i % 10000) + 1
            lot_code = f"LOT-{lot_id:06d}"
            t = time.perf_counter()
            rec = client.post(
                f"/api/warehouse/lots/{lot_code}/receipts",
                headers=tokens["whs1"],
                json={
                    "occurred_at": now_iso,
                    "counterpart_name": "NCC",
                    "lines": [{"lot_line_id": lot_id, "quantity": 5}],
                },
            )
            assert rec.status_code == 201, rec.text
            post_res = client.post(
                f"/api/warehouse/documents/{rec.json()['code']}/post",
                headers=tokens["whs1"],
            )
            assert post_res.status_code == 200
            d_wh_write.append(time.perf_counter() - t)
        results["WRITE: POST & confirm warehouse receipt (1M tx DB)"] = _stats_ms(d_wh_write)

        # Kiểm tra ngưỡng NFR-04: Read p95 <= 2000ms, Write p95 <= 3000ms.
        for name, stat in results.items():
            if name.startswith("READ:"):
                assert stat["p95_ms"] <= 2000.0, f"{name} vượt ngưỡng NFR-04 2000ms: {stat}"
            else:
                assert stat["p95_ms"] <= 3000.0, f"{name} vượt ngưỡng NFR-04 3000ms: {stat}"

        summary = {
            "orders_seeded": ORDERS_TARGET,
            "warehouse_transactions_seeded": WAREHOUSE_TX_TARGET,
            "seed_time_seconds": meta["seed_seconds"],
            "sample_runs_per_operation": SAMPLE_RUNS,
            "nfr04_passed": True,
            "operations": results,
        }
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        return summary


if __name__ == "__main__":
    run_benchmark()
