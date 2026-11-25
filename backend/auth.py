"""Xác thực phiên đăng nhập, phân quyền RBAC và hợp đồng truy vấn tài khoản (ADM-01/02 - BE3).

- Hỗ trợ phiên đăng nhập thật lưu trong bảng `user_sessions` (token >= 128 bit lưu dạng SHA-256).
- Hết hạn phiên sau 30 phút không hoạt động (`SESSION_IDLE_TIMEOUT`).
- Khi tài khoản bị khóa hoặc thu hồi vai trò, kiểm tra trực tiếp trên DB ở mỗi request
  nên mất quyền ngay lập tức ở request kế tiếp (NFR-02).
- Khi chạy dev/test với `DEV_AUTH=1` (hoặc `dev_header_provider`), nếu request có gửi
  `Authorization: Bearer <token>` hoặc cookie `session_token` thì luôn xác thực qua phiên thật;
  nếu không có mới fallback đọc `X-Dev-User` và `X-Dev-Roles`.
"""

import hashlib
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from fastapi import Request

from .db import connect, write_tx
from .errors import Conflict, Forbidden, NotFound, Unauthorized
from .timeutil import to_db

ALL_ROLES = frozenset({"SAL", "DSP", "WHS", "FIN", "MGR", "ADM"})
# Vận tải, thuê xe: DSP thao tác; SAL/MGR xem (TRN, RNT).
READ_ROLES = frozenset({"DSP", "SAL", "MGR"})
WRITE_ROLES = frozenset({"DSP"})
# Kho: WHS thao tác; MGR duyệt điều chỉnh; SAL/DSP xem phần được cấp (WHS).
WAREHOUSE_READ_ROLES = frozenset({"WHS", "MGR", "SAL", "DSP"})
WAREHOUSE_WRITE_ROLES = frozenset({"WHS"})
APPROVER_ROLES = frozenset({"MGR"})
# Tệp bằng chứng: người lập biên bản tải lên (DSP/WHS); người xem nghiệp vụ tải về (NFR-07).
ATTACHMENT_WRITE_ROLES = frozenset({"DSP", "WHS"})
ATTACHMENT_READ_ROLES = frozenset({"DSP", "WHS", "SAL", "MGR", "FIN"})
# Quản trị hệ thống, audit và CMS (ADM-01→04, PUB-05).
ADMIN_ROLES = frozenset({"ADM"})
AUDIT_READ_ROLES = frozenset({"ADM", "MGR"})
CMS_WRITE_ROLES = frozenset({"ADM", "MGR", "SAL"})

SESSION_IDLE_TIMEOUT = timedelta(minutes=30)
_PASSWORD_CHANGE_ALLOWED_PATHS = frozenset({
    "/api/auth/change-password",
    "/api/auth/logout",
    "/api/auth/me",
})


@dataclass(frozen=True)
class Principal:
    username: str
    roles: frozenset[str]


PrincipalProvider = Callable[[Request], Principal]


def deny_all_provider(request: Request) -> Principal:
    raise Unauthorized("Cần đăng nhập.")


def extract_session_token(request: Request) -> str | None:
    auth_header = (request.headers.get("Authorization") or "").strip()
    if auth_header:
        parts = auth_header.split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer" and parts[1].strip():
            return parts[1].strip()
        raise Unauthorized("Header Authorization không đúng định dạng Bearer token.")
    cookie_token = (request.cookies.get("session_token") or "").strip()
    if cookie_token:
        return cookie_token
    return None


def session_principal_provider(request: Request) -> Principal:
    token = extract_session_token(request)
    if not token:
        raise Unauthorized("Cần đăng nhập.")
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    now: datetime = request.app.state.clock()
    now_s = to_db(now)

    conn = connect(request.app.state.db_path)
    try:
        row = conn.execute(
            "SELECT s.id AS session_id, s.user_id, s.username, s.expires_at, s.revoked_at, "
            "u.status AS user_status, u.locked_until, u.must_change_password "
            "FROM user_sessions s JOIN users u ON u.id = s.user_id "
            "WHERE s.token_hash = ?",
            (token_hash,),
        ).fetchone()
        if row is None:
            raise Unauthorized("Phiên đăng nhập không hợp lệ hoặc đã hết hạn.")

        if row["user_status"] != "ACTIVE" or (
            row["locked_until"] is not None and row["locked_until"] > now_s
        ):
            if row["revoked_at"] is None:
                with write_tx(conn):
                    conn.execute(
                        "UPDATE user_sessions SET revoked_at = ?, revoke_reason = 'ACCOUNT_LOCKED' "
                        "WHERE id = ? AND revoked_at IS NULL",
                        (now_s, row["session_id"]),
                    )
            raise Forbidden("Tài khoản đã bị khóa.", code="ACCOUNT_LOCKED")

        if row["revoked_at"] is not None:
            raise Unauthorized("Phiên đăng nhập đã kết thúc hoặc bị thu hồi.", code="SESSION_REVOKED")

        if row["expires_at"] <= now_s:
            with write_tx(conn):
                conn.execute(
                    "UPDATE user_sessions SET revoked_at = ?, revoke_reason = 'IDLE_TIMEOUT' "
                    "WHERE id = ? AND revoked_at IS NULL",
                    (now_s, row["session_id"]),
                )
            raise Unauthorized("Phiên đăng nhập đã hết hạn do không hoạt động quá 30 phút.", code="SESSION_EXPIRED")

        if row["must_change_password"] == 1 and request.url.path not in _PASSWORD_CHANGE_ALLOWED_PATHS:
            raise Forbidden(
                "Tài khoản cần đổi mật khẩu lần đầu trước khi thực hiện thao tác này.",
                code="PASSWORD_CHANGE_REQUIRED",
            )

        roles = frozenset(
            r["role_code"]
            for r in conn.execute(
                "SELECT role_code FROM user_roles WHERE user_id = ?", (row["user_id"],)
            ).fetchall()
        )
        new_expires = to_db(now + SESSION_IDLE_TIMEOUT)
        with write_tx(conn):
            conn.execute(
                "UPDATE user_sessions SET last_seen_at = ?, expires_at = ? WHERE id = ?",
                (now_s, new_expires, row["session_id"]),
            )
        request.state.session_token_hash = token_hash
        request.state.user_id = row["user_id"]
        return Principal(username=row["username"], roles=roles)
    finally:
        conn.close()


def dev_header_provider(request: Request) -> Principal:
    if request.headers.get("Authorization") or request.cookies.get("session_token"):
        return session_principal_provider(request)
    user = (request.headers.get("X-Dev-User") or "").strip()
    if not user:
        raise Unauthorized("Cần đăng nhập.")
    roles = frozenset(
        r.strip().upper() for r in (request.headers.get("X-Dev-Roles") or "").split(",") if r.strip()
    )
    return Principal(username=user, roles=roles)


def current_principal(request: Request) -> Principal:
    return request.app.state.principal_provider(request)


def require_roles(principal: Principal, allowed: frozenset[str]) -> Principal:
    if not principal.roles & allowed:
        raise Forbidden("Tài khoản không có quyền thực hiện thao tác này.")
    return principal


def get_account_info(conn: sqlite3.Connection, username: str, *, now: datetime | None = None) -> dict | None:
    """Hợp đồng dùng chung cho các phân hệ (ví dụ BE1 SAL-03) truy vấn trạng thái và role của tài khoản."""
    row = conn.execute(
        "SELECT id, username, full_name, email, phone, status, locked_until, must_change_password "
        "FROM users WHERE username = ?",
        ((username or "").strip(),),
    ).fetchone()
    if row is None:
        return None
    roles = frozenset(
        r["role_code"]
        for r in conn.execute("SELECT role_code FROM user_roles WHERE user_id = ?", (row["id"],)).fetchall()
    )
    now_s = to_db(now) if now is not None else None
    temp_locked = bool(now_s and row["locked_until"] and row["locked_until"] > now_s)
    is_active = row["status"] == "ACTIVE" and not temp_locked
    return {
        "id": row["id"],
        "username": row["username"],
        "full_name": row["full_name"],
        "status": row["status"],
        "locked_until": row["locked_until"],
        "roles": sorted(roles),
        "is_active": is_active,
    }


def ensure_active_role(
    conn: sqlite3.Connection, username: str, role: str = "SAL", *, now: datetime | None = None
) -> None:
    """Kiểm tra tài khoản được gán đang ACTIVE và có vai trò yêu cầu (SAL-03, BE1_BE3_HANDOFF.md mục 1).

    Nếu bảng `users` chưa có dữ liệu (trong các unit test độc lập chỉ dùng header dev), bỏ qua để giữ
    tương thích ngược. Khi bảng `users` đã có tài khoản, bắt buộc username phải tồn tại, ACTIVE và có role.
    """
    has_users = conn.execute("SELECT EXISTS(SELECT 1 FROM users)").fetchone()[0]
    if not has_users:
        return
    info = get_account_info(conn, username, now=now)
    if info is None:
        raise NotFound(
            f"Không tìm thấy tài khoản nhân viên {username}.",
            details={"field": "assigned_to", "username": username},
        )
    if not info["is_active"]:
        raise Conflict(
            f"Tài khoản {username} đã bị khóa hoặc ngừng hoạt động.",
            code="USER_INACTIVE",
            details={"field": "assigned_to", "username": username},
        )
    if role not in info["roles"]:
        raise Conflict(
            f"Tài khoản {username} không có vai trò {role}.",
            code="USER_ROLE_MISMATCH",
            details={"field": "assigned_to", "username": username, "required_role": role},
        )
