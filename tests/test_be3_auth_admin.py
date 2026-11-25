"""Kiểm thử ADM-01→04, dịch vụ tệp đính kèm và hợp đồng kiểm tra tài khoản SAL-03 (BE3)."""

import sqlite3

import pytest

from backend import admin, sales
from backend.db import write_tx
from tests import factories as f
from tests.factories import ADM, DSP, SAL


def _seed_admin_and_staff(conn, now):
    with write_tx(conn):
        adm = admin.create_user(
            conn,
            username="adm1",
            full_name="Quản trị viên 1",
            password="Password@123",
            roles=["ADM"],
            must_change_password=False,
            actor="seed",
            now=now,
        )
        sal = admin.create_user(
            conn,
            username="sal1",
            full_name="Kinh doanh 1",
            password="Password@123",
            roles=["SAL"],
            must_change_password=False,
            actor="seed",
            now=now,
        )
        dsp = admin.create_user(
            conn,
            username="dsp1",
            full_name="Điều phối 1",
            password="Password@123",
            roles=["DSP"],
            must_change_password=False,
            actor="seed",
            now=now,
        )
    return adm, sal, dsp


def test_login_session_idle_timeout_and_logout(client, conn, clock):
    _seed_admin_and_staff(conn, clock())

    res = client.post("/api/auth/login", json={"username": "dsp1", "password": "Password@123"})
    assert res.status_code == 200, res.text
    token = res.json()["token"]
    assert len(token) >= 32
    headers = {"Authorization": f"Bearer {token}"}

    # Token không lưu plaintext trong DB.
    session_row = conn.execute("SELECT token_hash FROM user_sessions WHERE username = 'dsp1'").fetchone()
    assert session_row is not None and session_row["token_hash"] != token

    # Gọi API nghiệp vụ bằng phiên thật.
    assert client.get("/api/vehicles", headers=headers).status_code == 200

    # Sau 20 phút (< 30 phút), hoạt động gia hạn trượt phiên.
    clock.advance(minutes=20)
    assert client.get("/api/vehicles", headers=headers).status_code == 200

    # Sau 31 phút không hoạt động, phiên hết hạn.
    clock.advance(minutes=31)
    expired = client.get("/api/vehicles", headers=headers)
    assert expired.status_code == 401
    assert expired.json()["error"]["code"] == "SESSION_EXPIRED"

    # Đăng nhập lại rồi đăng xuất chủ động.
    res2 = client.post("/api/auth/login", json={"username": "dsp1", "password": "Password@123"})
    headers2 = {"Authorization": f"Bearer {res2.json()['token']}"}
    assert client.post("/api/auth/logout", headers=headers2).status_code == 200
    revoked = client.get("/api/vehicles", headers=headers2)
    assert revoked.status_code == 401
    assert revoked.json()["error"]["code"] == "SESSION_REVOKED"


def test_five_failed_logins_lock_account_for_15_minutes(client, conn, clock):
    _seed_admin_and_staff(conn, clock())

    for _ in range(1, 5):
        r = client.post("/api/auth/login", json={"username": "sal1", "password": "WrongPassword!"})
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "INVALID_CREDENTIALS"
        assert r.json()["error"]["details"] == {}

    fifth = client.post("/api/auth/login", json={"username": "sal1", "password": "WrongPassword!"})
    assert fifth.status_code == 403
    assert fifth.json()["error"]["code"] == "ACCOUNT_LOCKED"

    # Ngay cả khi nhập đúng mật khẩu trong lúc đang khóa tạm 15 phút vẫn bị từ chối.
    blocked = client.post("/api/auth/login", json={"username": "sal1", "password": "Password@123"})
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "ACCOUNT_LOCKED"

    # Sau 16 phút, hết thời gian khóa tạm, đăng nhập lại bình thường.
    clock.advance(minutes=16)
    recovered = client.post("/api/auth/login", json={"username": "sal1", "password": "Password@123"})
    assert recovered.status_code == 200


def test_first_login_requires_password_change(client, conn, clock):
    with write_tx(conn):
        admin.create_user(
            conn,
            username="newuser",
            full_name="Nhân viên Mới",
            password="InitPassword@1",
            roles=["DSP"],
            must_change_password=True,
            actor="adm1",
            now=clock(),
        )

    login_res = client.post("/api/auth/login", json={"username": "newuser", "password": "InitPassword@1"})
    assert login_res.status_code == 200
    assert login_res.json()["user"]["must_change_password"] is True
    headers = {"Authorization": f"Bearer {login_res.json()['token']}"}

    # Chưa đổi mật khẩu thì bị chặn gọi API nghiệp vụ.
    blocked = client.get("/api/vehicles", headers=headers)
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "PASSWORD_CHANGE_REQUIRED"

    # Đổi mật khẩu trùng mật khẩu cũ bị từ chối.
    same_pw = client.post(
        "/api/auth/change-password",
        headers=headers,
        json={"current_password": "InitPassword@1", "new_password": "InitPassword@1"},
    )
    assert same_pw.status_code == 422

    # Đổi sang mật khẩu mới hợp lệ -> mở khóa gọi API ngay.
    changed = client.post(
        "/api/auth/change-password",
        headers=headers,
        json={"current_password": "InitPassword@1", "new_password": "NewPassword@2"},
    )
    assert changed.status_code == 200
    assert changed.json()["must_change_password"] is False
    assert client.get("/api/vehicles", headers=headers).status_code == 200


def test_lock_account_and_revoke_role_take_effect_on_next_request_and_protect_last_admin(client, conn, clock):
    _seed_admin_and_staff(conn, clock())

    adm_token = client.post("/api/auth/login", json={"username": "adm1", "password": "Password@123"}).json()["token"]
    dsp_token = client.post("/api/auth/login", json={"username": "dsp1", "password": "Password@123"}).json()["token"]
    adm_headers = {"Authorization": f"Bearer {adm_token}"}
    dsp_headers = {"Authorization": f"Bearer {dsp_token}"}

    # Không cho khóa ADM đang hoạt động cuối cùng.
    adm_info = client.get("/api/admin/users/adm1", headers=adm_headers).json()
    lock_last_adm = client.post(
        "/api/admin/users/adm1/status",
        headers=adm_headers,
        json={"version": adm_info["version"], "status": "LOCKED", "reason": "Thử khóa ADM cuối"},
    )
    assert lock_last_adm.status_code == 409
    assert lock_last_adm.json()["error"]["code"] == "LAST_ADMIN_PROTECTED"

    # Không cho gỡ role ADM của ADM cuối cùng.
    strip_last_adm = client.put(
        "/api/admin/users/adm1/roles",
        headers=adm_headers,
        json={"version": adm_info["version"], "roles": ["SAL"], "reason": "Thử gỡ role ADM cuối"},
    )
    assert strip_last_adm.status_code == 409
    assert strip_last_adm.json()["error"]["code"] == "LAST_ADMIN_PROTECTED"

    # DSP đang gọi tạo xe bình thường.
    assert client.post(
        "/api/vehicles",
        headers=dsp_headers,
        json={"plate_number": "51C-777.01", "vehicle_type": "TRUCK_2T", "capacity_kg": 2000},
    ).status_code == 201

    # Thu hồi vai trò DSP (đổi sang SAL) -> request kế tiếp tạo xe bị chặn 403 ngay lập tức.
    dsp_info = client.get("/api/admin/users/dsp1", headers=adm_headers).json()
    role_changed = client.put(
        "/api/admin/users/dsp1/roles",
        headers=adm_headers,
        json={"version": dsp_info["version"], "roles": ["SAL"], "reason": "Luân chuyển bộ phận"},
    )
    assert role_changed.status_code == 200
    denied_write = client.post(
        "/api/vehicles",
        headers=dsp_headers,
        json={"plate_number": "51C-777.02", "vehicle_type": "TRUCK_2T", "capacity_kg": 2000},
    )
    assert denied_write.status_code == 403

    # Khóa tài khoản dsp1 -> request kế tiếp mất quyền ngay lập tức kể cả GET.
    dsp_info2 = client.get("/api/admin/users/dsp1", headers=adm_headers).json()
    locked = client.post(
        "/api/admin/users/dsp1/status",
        headers=adm_headers,
        json={"version": dsp_info2["version"], "status": "LOCKED", "reason": "Nghỉ việc"},
    )
    assert locked.status_code == 200
    next_req = client.get("/api/vehicles", headers=dsp_headers)
    assert next_req.status_code == 403
    assert next_req.json()["error"]["code"] == "ACCOUNT_LOCKED"


def test_sal03_assignee_must_be_active_and_have_sal_role(client, conn, clock):
    _seed_admin_and_staff(conn, clock())
    with write_tx(conn):
        admin.create_user(
            conn,
            username="sal_locked",
            full_name="Kinh doanh bị khóa",
            password="Password@123",
            roles=["SAL"],
            status="LOCKED",
            must_change_password=False,
            actor="adm1",
            now=clock(),
        )
        req = sales.create_request(
            conn,
            service_type="TRANSPORT",
            source="INTERNAL",
            contact={"name": "KH A", "contact_name": "A", "phone": "0901112233"},
            service_data={
                "pickup_address": "A",
                "delivery_address": "B",
                "cargo_description": "Hàng",
                "cargo_weight_kg": 500,
                "planned_pickup_at": f.iso(8),
            },
            actor="sal1",
            now=clock(),
        )

    # Gán cho tài khoản không có vai trò SAL (dsp1) -> 409 USER_ROLE_MISMATCH.
    bad_role = client.patch(
        f"/api/sales/requests/{req['code']}",
        headers=SAL,
        json={"version": req["version"], "assigned_to": "dsp1"},
    )
    assert bad_role.status_code == 409
    assert bad_role.json()["error"]["code"] == "USER_ROLE_MISMATCH"

    # Gán cho tài khoản SAL đang bị LOCKED -> 409 USER_INACTIVE.
    locked_res = client.patch(
        f"/api/sales/requests/{req['code']}",
        headers=SAL,
        json={"version": req["version"], "assigned_to": "sal_locked"},
    )
    assert locked_res.status_code == 409
    assert locked_res.json()["error"]["code"] == "USER_INACTIVE"

    # Gán cho sal1 đang ACTIVE và có role SAL -> 200 OK.
    ok_res = client.patch(
        f"/api/sales/requests/{req['code']}",
        headers=SAL,
        json={"version": req["version"], "assigned_to": "sal1"},
    )
    assert ok_res.status_code == 200
    assert ok_res.json()["assigned_to"] == "sal1"


def test_shared_catalogs_deactivate_instead_of_delete_and_audit_search(client, conn):
    # ADM tạo loại xe và đơn vị hàng hóa.
    vt = client.post(
        "/api/admin/catalogs/vehicle-types",
        headers=ADM,
        json={"code": "truck_2t", "name": "Xe tải 2 tấn", "default_capacity_kg": 2000},
    )
    assert vt.status_code == 201
    assert vt.json()["code"] == "TRUCK_2T"

    # Non-ADM không được tạo danh mục.
    assert client.post(
        "/api/admin/catalogs/vehicle-types",
        headers=SAL,
        json={"code": "TRUCK_5T", "name": "Xe tải 5 tấn", "default_capacity_kg": 5000},
    ).status_code == 403

    unit = client.post(
        "/api/admin/catalogs/units",
        headers=ADM,
        json={"code": "kien", "name": "Kiện tiêu chuẩn"},
    )
    assert unit.status_code == 201
    assert unit.json()["code"] == "KIEN"

    # Ngừng sử dụng thay vì xóa; trigger DB chặn DELETE trực tiếp.
    deact = client.post(
        "/api/admin/catalogs/units/KIEN/status",
        headers=ADM,
        json={"version": unit.json()["version"], "status": "INACTIVE", "reason": "Đổi quy chuẩn"},
    )
    assert deact.status_code == 200
    assert deact.json()["status"] == "INACTIVE"

    with pytest.raises(sqlite3.IntegrityError, match="CATALOG_NO_DELETE"):
        conn.execute("DELETE FROM catalog_units WHERE code = 'KIEN'")
    with pytest.raises(sqlite3.IntegrityError, match="CATALOG_NO_DELETE"):
        conn.execute("DELETE FROM catalog_vehicle_types WHERE code = 'TRUCK_2T'")

    # Tra cứu audit log (ADM-04): lọc theo object_type và kiểm tra không có API xóa/sửa.
    logs = client.get("/api/admin/audit-logs", headers=ADM, params={"object_type": "Unit", "object_code": "KIEN"})
    assert logs.status_code == 200
    assert logs.json()["total"] == 2
    assert client.get("/api/admin/audit-logs", headers=DSP).status_code == 403
    assert client.delete("/api/admin/audit-logs/1", headers=ADM).status_code == 405
