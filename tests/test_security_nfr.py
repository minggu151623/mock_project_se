"""Kiểm thử bảo mật, phân quyền RBAC, NFR-07/08/10 và seed dữ liệu mẫu 3 dịch vụ (BE3)."""

from fastapi.testclient import TestClient

from backend import seed_be3_dev
from backend.main import create_app
from tests import factories as f
from tests.factories import ADM, DSP, SAL, WHS


def test_production_mode_rejects_dev_headers_and_requires_real_session(db_path, clock, files_dir):
    # Khi không truyền dev_header_provider (và DEV_AUTH không bật), X-Dev-User bị từ chối 401.
    prod_app = create_app(db_path, files_dir=files_dir, clock=clock)
    prod_client = TestClient(prod_app)

    res = prod_client.get("/api/vehicles", headers=DSP)
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "UNAUTHORIZED"


def test_request_id_header_and_internal_error_hides_stack_trace(db_path, clock, files_dir):
    app = create_app(db_path, files_dir=files_dir, clock=clock)

    @app.get("/api/_boom")
    def boom():
        raise RuntimeError("secret-internal-database-path-and-stack-trace")

    c = TestClient(app, raise_server_exceptions=False)
    r = c.get("/api/_boom")
    assert r.status_code == 500
    assert "X-Request-ID" in r.headers
    body = r.json()
    assert body["error"]["code"] == "INTERNAL_ERROR"
    assert r.headers["X-Request-ID"] in body["error"]["message"]
    assert "secret-internal-database-path" not in r.text
    assert "Traceback" not in r.text


def test_attachment_download_sets_nosniff_and_enforces_rbac(client):
    code = f.upload(client, headers=DSP, name="bien-ban.png", data=f.PNG)

    # Người có quyền đọc nghiệp vụ tải về có header X-Content-Type-Options: nosniff.
    dl = client.get(f"/api/attachments/{code}", headers=SAL)
    assert dl.status_code == 200
    assert dl.headers.get("x-content-type-options") == "nosniff"

    # ADM không mặc nhiên có quyền tải chứng từ nghiệp vụ.
    assert client.get(f"/api/attachments/{code}", headers=ADM).status_code == 403
    # Khách không đăng nhập không tải được tệp nội bộ.
    assert client.get(f"/api/attachments/{code}").status_code == 401


def test_seed_be3_dev_creates_confirmed_orders_for_all_three_services(tmp_path):
    seed_db = str(tmp_path / "seed_test.sqlite3")
    result = seed_be3_dev.seed(seed_db)
    assert result["skipped"] is False
    assert len(result["lookup_samples"]) == 3

    app = create_app(seed_db, files_dir=str(tmp_path / "files"))
    c = TestClient(app)

    # Kiểm tra đăng nhập thật bằng tài khoản seed và tra cứu cả 3 đơn CONFIRMED.
    login_sal = c.post("/api/auth/login", json={"username": "sal1", "password": seed_be3_dev.DEFAULT_PASSWORD})
    assert login_sal.status_code == 200
    sal_headers = {"Authorization": f"Bearer {login_sal.json()['token']}"}

    orders = c.get("/api/sales/orders", headers=sal_headers, params={"status": "CONFIRMED"}).json()
    service_types = {item["service_type"] for item in orders["items"]}
    assert {"TRANSPORT", "RENTAL", "WAREHOUSE"} <= service_types

    for sample in result["lookup_samples"]:
        lookup = c.post(
            "/api/public/lookup",
            json={"request_code": sample["request_code"], "token": sample["lookup_token"]},
        )
        assert lookup.status_code == 200
        assert lookup.json()["order_code"] == sample["order_code"]
        assert lookup.json()["order_status"] == "CONFIRMED"
