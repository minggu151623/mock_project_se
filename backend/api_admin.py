"""HTTP API cho xác thực phiên, tài khoản, phân quyền RBAC, danh mục dùng chung và tra cứu audit (ADM-01→04 - BE3)."""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import Field

from . import admin, audit
from .api_common import Body, Limit, NameText, NoteText, Offset, get_conn, now_of, role_dependency
from .auth import ADMIN_ROLES, ALL_ROLES, AUDIT_READ_ROLES, Principal, current_principal
from .db import write_tx

router = APIRouter()
admin_only = role_dependency(ADMIN_ROLES)
any_staff = role_dependency(ALL_ROLES)
audit_reader = role_dependency(AUDIT_READ_ROLES)


# ---------- ADM-01: Đăng nhập, đăng xuất, đổi mật khẩu ----------

class LoginIn(Body):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=128)


class PasswordChangeIn(Body):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=1, max_length=128)


@router.post("/auth/login")
def login(body: LoginIn, request: Request, response: Response, conn=Depends(get_conn)):
    result = admin.login(conn, username=body.username, password=body.password, now=now_of(request))
    response.set_cookie(
        key="session_token",
        value=result["token"],
        httponly=True,
        samesite="lax",
        path="/",
    )
    return result


@router.post("/auth/logout")
def logout(
    request: Request,
    response: Response,
    p: Principal = Depends(current_principal),
    conn=Depends(get_conn),
):
    token_hash = getattr(request.state, "session_token_hash", None)
    with write_tx(conn):
        result = admin.logout(conn, actor=p.username, now=now_of(request), token_hash=token_hash)
    response.delete_cookie(key="session_token", path="/")
    return result


@router.get("/auth/me")
def me(p: Principal = Depends(current_principal), conn=Depends(get_conn)):
    row = conn.execute("SELECT * FROM users WHERE username = ?", (p.username,)).fetchone()
    if row is not None:
        return admin.user_to_dict(conn, row)
    return {
        "username": p.username,
        "roles": sorted(p.roles),
        "status": "ACTIVE",
        "must_change_password": False,
    }


@router.post("/auth/change-password")
def change_password(
    body: PasswordChangeIn,
    request: Request,
    p: Principal = Depends(current_principal),
    conn=Depends(get_conn),
):
    token_hash = getattr(request.state, "session_token_hash", None)
    with write_tx(conn):
        return admin.change_password(
            conn,
            username=p.username,
            current_password=body.current_password,
            new_password=body.new_password,
            now=now_of(request),
            keep_token_hash=token_hash,
        )


# ---------- ADM-02: Tài khoản và phân quyền RBAC ----------

class UserCreateIn(Body):
    username: str = Field(min_length=3, max_length=50)
    full_name: NameText
    password: str = Field(min_length=1, max_length=128)
    roles: list[str] = Field(min_length=1, max_length=6)
    email: str | None = Field(None, max_length=254)
    phone: str | None = Field(None, max_length=30)
    status: Literal["ACTIVE", "LOCKED"] = "ACTIVE"
    must_change_password: bool = True


class UserUpdateIn(Body):
    version: int = Field(ge=1)
    full_name: str | None = Field(None, min_length=1, max_length=200)
    email: str | None = Field(None, max_length=254)
    phone: str | None = Field(None, max_length=30)


class UserStatusIn(Body):
    version: int = Field(ge=1)
    status: Literal["ACTIVE", "LOCKED"]
    reason: str | None = Field(None, max_length=2000)


class UserRolesIn(Body):
    version: int = Field(ge=1)
    roles: list[str] = Field(min_length=1, max_length=6)
    reason: str | None = Field(None, max_length=2000)


class UserResetPasswordIn(Body):
    version: int = Field(ge=1)
    new_password: str = Field(min_length=1, max_length=128)
    reason: str | None = Field(None, max_length=2000)


@router.post("/admin/users", status_code=201)
def create_user(
    body: UserCreateIn,
    request: Request,
    p: Principal = Depends(admin_only),
    conn=Depends(get_conn),
):
    with write_tx(conn):
        return admin.create_user(conn, **body.model_dump(), actor=p.username, now=now_of(request))


@router.get("/admin/users")
def list_users(
    status: Literal["ACTIVE", "LOCKED"] | None = None,
    role: str | None = None,
    q: str | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
    _: Principal = Depends(admin_only),
    conn=Depends(get_conn),
):
    return admin.list_users(conn, status=status, role=role, q=q, limit=limit, offset=offset)


@router.get("/admin/users/{username}")
def get_user(username: str, _: Principal = Depends(admin_only), conn=Depends(get_conn)):
    return admin.get_user(conn, username)


@router.patch("/admin/users/{username}")
def update_user(
    username: str,
    body: UserUpdateIn,
    request: Request,
    p: Principal = Depends(admin_only),
    conn=Depends(get_conn),
):
    with write_tx(conn):
        return admin.update_user(
            conn,
            username,
            expected_version=body.version,
            full_name=body.full_name,
            email=body.email,
            phone=body.phone,
            actor=p.username,
            now=now_of(request),
        )


@router.post("/admin/users/{username}/status")
def set_user_status(
    username: str,
    body: UserStatusIn,
    request: Request,
    p: Principal = Depends(admin_only),
    conn=Depends(get_conn),
):
    with write_tx(conn):
        return admin.set_user_status(
            conn,
            username,
            expected_version=body.version,
            status=body.status,
            reason=body.reason,
            actor=p.username,
            now=now_of(request),
        )


@router.put("/admin/users/{username}/roles")
def set_user_roles(
    username: str,
    body: UserRolesIn,
    request: Request,
    p: Principal = Depends(admin_only),
    conn=Depends(get_conn),
):
    with write_tx(conn):
        return admin.set_user_roles(
            conn,
            username,
            expected_version=body.version,
            roles=body.roles,
            reason=body.reason,
            actor=p.username,
            now=now_of(request),
        )


@router.post("/admin/users/{username}/reset-password")
def reset_user_password(
    username: str,
    body: UserResetPasswordIn,
    request: Request,
    p: Principal = Depends(admin_only),
    conn=Depends(get_conn),
):
    with write_tx(conn):
        return admin.reset_user_password(
            conn,
            username,
            expected_version=body.version,
            new_password=body.new_password,
            reason=body.reason,
            actor=p.username,
            now=now_of(request),
        )


# ---------- ADM-03: Danh mục dùng chung ----------

class VehicleTypeCreateIn(Body):
    code: str = Field(min_length=2, max_length=50)
    name: NameText
    default_capacity_kg: int = Field(gt=0)
    description: NoteText = ""
    status: Literal["ACTIVE", "INACTIVE"] = "ACTIVE"


class VehicleTypeUpdateIn(Body):
    version: int = Field(ge=1)
    name: str | None = Field(None, min_length=1, max_length=200)
    default_capacity_kg: int | None = Field(None, gt=0)
    description: str | None = Field(None, max_length=2000)


class CatalogStatusIn(Body):
    version: int = Field(ge=1)
    status: Literal["ACTIVE", "INACTIVE"]
    reason: str | None = Field(None, max_length=2000)


class UnitCreateIn(Body):
    code: str = Field(min_length=2, max_length=50)
    name: NameText
    description: NoteText = ""
    status: Literal["ACTIVE", "INACTIVE"] = "ACTIVE"


class UnitUpdateIn(Body):
    version: int = Field(ge=1)
    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, max_length=2000)


class ServiceCatalogUpdateIn(Body):
    version: int | None = Field(None, ge=1)
    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, max_length=2000)
    warehouse_address: str | None = Field(None, max_length=2000)
    status: Literal["ACTIVE", "INACTIVE"] | None = None


@router.get("/admin/catalogs/vehicle-types")
def list_vehicle_types(
    status: Literal["ACTIVE", "INACTIVE"] | None = None,
    q: str | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
    _: Principal = Depends(any_staff),
    conn=Depends(get_conn),
):
    return admin.list_vehicle_types(conn, status=status, q=q, limit=limit, offset=offset)


@router.post("/admin/catalogs/vehicle-types", status_code=201)
def create_vehicle_type(
    body: VehicleTypeCreateIn,
    request: Request,
    p: Principal = Depends(admin_only),
    conn=Depends(get_conn),
):
    with write_tx(conn):
        return admin.create_vehicle_type(conn, **body.model_dump(), actor=p.username, now=now_of(request))


@router.patch("/admin/catalogs/vehicle-types/{code}")
def update_vehicle_type(
    code: str,
    body: VehicleTypeUpdateIn,
    request: Request,
    p: Principal = Depends(admin_only),
    conn=Depends(get_conn),
):
    with write_tx(conn):
        return admin.update_vehicle_type(
            conn,
            code,
            expected_version=body.version,
            name=body.name,
            default_capacity_kg=body.default_capacity_kg,
            description=body.description,
            actor=p.username,
            now=now_of(request),
        )


@router.post("/admin/catalogs/vehicle-types/{code}/status")
def set_vehicle_type_status(
    code: str,
    body: CatalogStatusIn,
    request: Request,
    p: Principal = Depends(admin_only),
    conn=Depends(get_conn),
):
    with write_tx(conn):
        return admin.set_vehicle_type_status(
            conn,
            code,
            expected_version=body.version,
            status=body.status,
            reason=body.reason,
            actor=p.username,
            now=now_of(request),
        )


@router.get("/admin/catalogs/units")
def list_units(
    status: Literal["ACTIVE", "INACTIVE"] | None = None,
    q: str | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
    _: Principal = Depends(any_staff),
    conn=Depends(get_conn),
):
    return admin.list_units(conn, status=status, q=q, limit=limit, offset=offset)


@router.post("/admin/catalogs/units", status_code=201)
def create_unit(
    body: UnitCreateIn,
    request: Request,
    p: Principal = Depends(admin_only),
    conn=Depends(get_conn),
):
    with write_tx(conn):
        return admin.create_unit(conn, **body.model_dump(), actor=p.username, now=now_of(request))


@router.patch("/admin/catalogs/units/{code}")
def update_unit(
    code: str,
    body: UnitUpdateIn,
    request: Request,
    p: Principal = Depends(admin_only),
    conn=Depends(get_conn),
):
    with write_tx(conn):
        return admin.update_unit(
            conn,
            code,
            expected_version=body.version,
            name=body.name,
            description=body.description,
            actor=p.username,
            now=now_of(request),
        )


@router.post("/admin/catalogs/units/{code}/status")
def set_unit_status(
    code: str,
    body: CatalogStatusIn,
    request: Request,
    p: Principal = Depends(admin_only),
    conn=Depends(get_conn),
):
    with write_tx(conn):
        return admin.set_unit_status(
            conn,
            code,
            expected_version=body.version,
            status=body.status,
            reason=body.reason,
            actor=p.username,
            now=now_of(request),
        )


@router.get("/admin/catalogs/services")
def list_services(
    status: Literal["ACTIVE", "INACTIVE"] | None = None,
    _: Principal = Depends(any_staff),
    conn=Depends(get_conn),
):
    return admin.list_service_catalogs(conn, status=status)


@router.patch("/admin/catalogs/services/{code}")
def update_service(
    code: str,
    body: ServiceCatalogUpdateIn,
    request: Request,
    p: Principal = Depends(admin_only),
    conn=Depends(get_conn),
):
    with write_tx(conn):
        return admin.upsert_service_catalog(
            conn,
            code,
            expected_version=body.version,
            name=body.name,
            description=body.description,
            warehouse_address=body.warehouse_address,
            status=body.status,
            actor=p.username,
            now=now_of(request),
        )


# ---------- ADM-04: Tra cứu lịch sử thao tác (Audit Log - chỉ đọc) ----------

@router.get("/admin/audit-logs")
def list_audit_logs(
    object_type: str | None = None,
    object_code: str | None = None,
    actor: str | None = None,
    action: str | None = None,
    from_at: datetime | None = None,
    to_at: datetime | None = None,
    q: str | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
    _: Principal = Depends(audit_reader),
    conn=Depends(get_conn),
):
    return audit.list_audit_logs(
        conn,
        object_type=object_type,
        object_code=object_code,
        actor=actor,
        action=action,
        from_at=from_at,
        to_at=to_at,
        q=q,
        limit=limit,
        offset=offset,
    )


@router.get("/admin/audit-logs/{audit_id}")
def get_audit_log(
    audit_id: int,
    _: Principal = Depends(audit_reader),
    conn=Depends(get_conn),
):
    return audit.get_audit_log(conn, audit_id)
