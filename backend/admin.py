"""Nghiệp vụ quản trị hệ thống: đăng nhập, phiên, tài khoản, phân quyền RBAC và danh mục dùng chung (ADM-01→04 - BE3)."""

import hashlib
import hmac
import re
import secrets
import sqlite3
from datetime import datetime, timedelta

from .audit import write_audit
from .auth import ALL_ROLES, SESSION_IDLE_TIMEOUT
from .db import require_tx, write_tx
from .errors import Conflict, Forbidden, NotFound, Unauthorized, ValidationFailed
from .timeutil import from_db, to_db, to_utc
from .validation import NAME_MAX, NOTE_MAX, check_version, clean_text

PBKDF2_ITERATIONS = 120_000
MAX_FAILED_LOGINS = 5
LOGIN_FAILURE_WINDOW = timedelta(minutes=15)
LOCKOUT_DURATION = timedelta(minutes=15)

USER_STATUSES = ("ACTIVE", "LOCKED")
CATALOG_STATUSES = ("ACTIVE", "INACTIVE")
SERVICE_CODES = ("TRANSPORT", "RENTAL", "WAREHOUSE")

_USERNAME_RE = re.compile(r"^[a-z0-9._\-]{3,50}$")
_CODE_RE = re.compile(r"^[A-Z0-9_\-]{2,50}$")
_PHONE_SEPARATORS = re.compile(r"[\s\-.()]")
_PHONE_RE = re.compile(r"^\+?\d{8,15}$")


def hash_password(password: str, *, salt_hex: str | None = None, iterations: int = PBKDF2_ITERATIONS) -> str:
    salt_hex = salt_hex or secrets.token_hex(16)
    derived = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt_hex),
        iterations,
    ).hex()
    return f"pbkdf2_sha256${iterations}${salt_hex}${derived}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algo, iter_s, salt_hex, _ = stored_hash.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        expected = hash_password(password, salt_hex=salt_hex, iterations=int(iter_s))
        return hmac.compare_digest(expected, stored_hash)
    except (ValueError, TypeError):
        return False


def _validate_password(password: str | None, field: str = "password") -> str:
    raw = password or ""
    if len(raw) < 8 or len(raw) > 128 or not raw.strip():
        raise ValidationFailed(
            "Mật khẩu phải từ 8 đến 128 ký tự và không chỉ gồm khoảng trắng.",
            details={"field": field},
        )
    return raw


def _validate_username(username: str | None) -> str:
    cleaned = (username or "").strip().lower()
    if not _USERNAME_RE.match(cleaned):
        raise ValidationFailed(
            "Tên đăng nhập gồm 3–50 ký tự chữ thường, số, dấu chấm, gạch dưới hoặc gạch ngang.",
            details={"field": "username"},
        )
    return cleaned


def _validate_catalog_code(code: str | None, field: str = "code") -> str:
    cleaned = (code or "").strip().upper()
    if not _CODE_RE.match(cleaned):
        raise ValidationFailed(
            f"Mã {field} gồm 2–50 ký tự chữ in hoa, số, gạch dưới hoặc gạch ngang.",
            details={"field": field},
        )
    return cleaned


def _validate_email(email: str | None) -> str | None:
    if email is None or not str(email).strip():
        return None
    cleaned = clean_text(str(email), "email", max_len=254)
    if "@" not in cleaned or cleaned.startswith("@") or cleaned.endswith("@"):
        raise ValidationFailed("Email không đúng định dạng.", details={"field": "email"})
    return cleaned


def _validate_phone(phone: str | None) -> str | None:
    if phone is None or not str(phone).strip():
        return None
    cleaned = _PHONE_SEPARATORS.sub("", str(phone).strip())
    if not _PHONE_RE.match(cleaned):
        raise ValidationFailed(
            "Số điện thoại gồm 8–15 chữ số, có thể có dấu + ở đầu.",
            details={"field": "phone"},
        )
    return cleaned


def _validate_roles(roles: list[str] | set[str] | frozenset[str] | None) -> list[str]:
    normalized = sorted({(r or "").strip().upper() for r in (roles or []) if (r or "").strip()})
    if not normalized:
        raise ValidationFailed("Tài khoản phải có ít nhất một vai trò.", details={"field": "roles"})
    invalid = [r for r in normalized if r not in ALL_ROLES]
    if invalid:
        raise ValidationFailed(
            f"Vai trò không hợp lệ: {', '.join(invalid)}.",
            details={"field": "roles", "invalid_roles": invalid},
        )
    return normalized


def _user_roles(conn: sqlite3.Connection, user_id: int) -> list[str]:
    rows = conn.execute(
        "SELECT role_code FROM user_roles WHERE user_id = ? ORDER BY role_code",
        (user_id,),
    ).fetchall()
    return [r["role_code"] for r in rows]


def _user_row_by_username(conn: sqlite3.Connection, username: str) -> sqlite3.Row:
    cleaned = (username or "").strip().lower()
    row = conn.execute("SELECT * FROM users WHERE username = ?", (cleaned,)).fetchone()
    if row is None:
        raise NotFound("Không tìm thấy tài khoản.", details={"username": cleaned})
    return row


def user_to_dict(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    roles = _user_roles(conn, row["id"])
    return {
        "id": row["id"],
        "username": row["username"],
        "full_name": row["full_name"],
        "email": row["email"],
        "phone": row["phone"],
        "status": row["status"],
        "roles": roles,
        "must_change_password": bool(row["must_change_password"]),
        "failed_login_count": row["failed_login_count"],
        "locked_until": row["locked_until"],
        "lock_reason": row["lock_reason"],
        "version": row["version"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _count_other_active_admins(conn: sqlite3.Connection, exclude_user_id: int) -> int:
    row = conn.execute(
        "SELECT COUNT(DISTINCT u.id) FROM users u "
        "JOIN user_roles ur ON ur.user_id = u.id "
        "WHERE u.status = 'ACTIVE' AND ur.role_code = 'ADM' AND u.id <> ?",
        (exclude_user_id,),
    ).fetchone()
    return row[0] if row else 0


def _ensure_not_last_admin(conn: sqlite3.Connection, user_row: sqlite3.Row, current_roles: list[str]) -> None:
    if user_row["status"] == "ACTIVE" and "ADM" in current_roles:
        if _count_other_active_admins(conn, user_row["id"]) == 0:
            raise Conflict(
                "Không thể khóa hoặc thu hồi quyền ADM của quản trị viên đang hoạt động cuối cùng.",
                code="LAST_ADMIN_PROTECTED",
                details={"username": user_row["username"]},
            )


def _revoke_user_sessions(
    conn: sqlite3.Connection, user_id: int, *, reason: str, now: datetime, exclude_token_hash: str | None = None
) -> int:
    if exclude_token_hash:
        cur = conn.execute(
            "UPDATE user_sessions SET revoked_at = ?, revoke_reason = ? "
            "WHERE user_id = ? AND revoked_at IS NULL AND token_hash <> ?",
            (to_db(now), reason, user_id, exclude_token_hash),
        )
    else:
        cur = conn.execute(
            "UPDATE user_sessions SET revoked_at = ?, revoke_reason = ? "
            "WHERE user_id = ? AND revoked_at IS NULL",
            (to_db(now), reason, user_id),
        )
    return cur.rowcount


# ---------- ADM-01: Đăng nhập, đăng xuất, đổi mật khẩu, quản lý phiên ----------

def login(conn: sqlite3.Connection, *, username: str, password: str, now: datetime) -> dict:
    """Đăng nhập và tạo phiên mới.

    Quản lý transaction nội bộ để khi đăng nhập sai, số lần sai và trạng thái khóa tạm 15 phút
    vẫn được COMMIT trước khi ném lỗi 401/403.
    """
    cleaned_user = (username or "").strip().lower()
    if not cleaned_user:
        raise ValidationFailed("Trường username là bắt buộc.", details={"field": "username"})
    if not password:
        raise ValidationFailed("Trường password là bắt buộc.", details={"field": "password"})

    now_u = to_utc(now)
    now_s = to_db(now_u)
    error_to_raise: Exception | None = None
    result: dict | None = None

    with write_tx(conn):
        row = conn.execute("SELECT * FROM users WHERE username = ?", (cleaned_user,)).fetchone()
        if row is None:
            write_audit(
                conn,
                actor=cleaned_user,
                action="AUTH_LOGIN_FAILED",
                object_type="User",
                object_code=cleaned_user,
                at=now_u,
                reason="Tài khoản không tồn tại",
            )
            error_to_raise = Unauthorized(
                "Tên đăng nhập hoặc mật khẩu không đúng.", code="INVALID_CREDENTIALS"
            )
        elif row["status"] != "ACTIVE":
            write_audit(
                conn,
                actor=cleaned_user,
                action="AUTH_LOGIN_BLOCKED",
                object_type="User",
                object_code=cleaned_user,
                at=now_u,
                reason="Tài khoản đang ở trạng thái LOCKED",
            )
            error_to_raise = Forbidden(
                "Tài khoản đã bị khóa. Vui lòng liên hệ quản trị viên.",
                code="ACCOUNT_LOCKED",
            )
        elif row["locked_until"] is not None and row["locked_until"] > now_s:
            write_audit(
                conn,
                actor=cleaned_user,
                action="AUTH_LOGIN_BLOCKED",
                object_type="User",
                object_code=cleaned_user,
                at=now_u,
                reason="Tài khoản đang trong thời gian khóa tạm thời",
            )
            error_to_raise = Forbidden(
                "Tài khoản đang bị khóa tạm thời do đăng nhập sai nhiều lần.",
                code="ACCOUNT_LOCKED",
                details={"locked_until": row["locked_until"]},
            )
        elif not verify_password(password, row["password_hash"]):
            first_failed = from_db(row["first_failed_at"]) if row["first_failed_at"] else None
            if first_failed is None or (first_failed + LOGIN_FAILURE_WINDOW) <= now_u:
                failed_count = 1
                first_failed_s = now_s
            else:
                failed_count = row["failed_login_count"] + 1
                first_failed_s = row["first_failed_at"]

            if failed_count >= MAX_FAILED_LOGINS:
                locked_until_s = to_db(now_u + LOCKOUT_DURATION)
                conn.execute(
                    "UPDATE users SET failed_login_count = ?, first_failed_at = ?, locked_until = ?, "
                    "lock_reason = ?, updated_at = ?, updated_by = ? WHERE id = ?",
                    (
                        failed_count,
                        first_failed_s,
                        locked_until_s,
                        "Đăng nhập sai 5 lần liên tiếp trong 15 phút",
                        now_s,
                        cleaned_user,
                        row["id"],
                    ),
                )
                _revoke_user_sessions(conn, row["id"], reason="LOGIN_LOCKOUT", now=now_u)
                write_audit(
                    conn,
                    actor=cleaned_user,
                    action="AUTH_LOGIN_LOCKED",
                    object_type="User",
                    object_code=cleaned_user,
                    at=now_u,
                    after={"failed_login_count": failed_count, "locked_until": locked_until_s},
                    reason="Khóa tạm 15 phút sau 5 lần đăng nhập sai",
                )
                error_to_raise = Forbidden(
                    "Tài khoản bị khóa tạm thời 15 phút do nhập sai mật khẩu 5 lần liên tiếp.",
                    code="ACCOUNT_LOCKED",
                    details={"locked_until": locked_until_s, "failed_attempts": failed_count},
                )
            else:
                conn.execute(
                    "UPDATE users SET failed_login_count = ?, first_failed_at = ?, locked_until = NULL, "
                    "updated_at = ?, updated_by = ? WHERE id = ?",
                    (failed_count, first_failed_s, now_s, cleaned_user, row["id"]),
                )
                write_audit(
                    conn,
                    actor=cleaned_user,
                    action="AUTH_LOGIN_FAILED",
                    object_type="User",
                    object_code=cleaned_user,
                    at=now_u,
                    after={"failed_login_count": failed_count},
                )
                error_to_raise = Unauthorized(
                    "Tên đăng nhập hoặc mật khẩu không đúng.",
                    code="INVALID_CREDENTIALS",
                )
        else:
            conn.execute(
                "UPDATE users SET failed_login_count = 0, first_failed_at = NULL, locked_until = NULL, "
                "lock_reason = NULL, updated_at = ?, updated_by = ? WHERE id = ?",
                (now_s, cleaned_user, row["id"]),
            )
            raw_token = secrets.token_urlsafe(32)
            token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
            expires_s = to_db(now_u + SESSION_IDLE_TIMEOUT)
            conn.execute(
                "INSERT INTO user_sessions(token_hash, user_id, username, created_at, last_seen_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (token_hash, row["id"], row["username"], now_s, now_s, expires_s),
            )
            updated_row = _user_row_by_username(conn, cleaned_user)
            write_audit(
                conn,
                actor=cleaned_user,
                action="AUTH_LOGIN",
                object_type="User",
                object_code=cleaned_user,
                at=now_u,
                after={"expires_at": expires_s},
            )
            result = {
                "token": raw_token,
                "token_type": "Bearer",
                "expires_at": expires_s,
                "user": user_to_dict(conn, updated_row),
            }

    if error_to_raise is not None:
        raise error_to_raise
    assert result is not None
    return result


def logout(
    conn: sqlite3.Connection, *, actor: str, now: datetime, token_hash: str | None = None
) -> dict:
    require_tx(conn)
    now_s = to_db(now)
    if token_hash:
        conn.execute(
            "UPDATE user_sessions SET revoked_at = ?, revoke_reason = 'LOGOUT' "
            "WHERE token_hash = ? AND revoked_at IS NULL",
            (now_s, token_hash),
        )
    else:
        conn.execute(
            "UPDATE user_sessions SET revoked_at = ?, revoke_reason = 'LOGOUT' "
            "WHERE username = ? AND revoked_at IS NULL",
            (now_s, actor),
        )
    write_audit(
        conn,
        actor=actor,
        action="AUTH_LOGOUT",
        object_type="User",
        object_code=actor,
        at=now,
    )
    return {"status": "LOGGED_OUT"}


def change_password(
    conn: sqlite3.Connection,
    *,
    username: str,
    current_password: str,
    new_password: str,
    now: datetime,
    keep_token_hash: str | None = None,
) -> dict:
    require_tx(conn)
    row = _user_row_by_username(conn, username)
    if not verify_password(current_password or "", row["password_hash"]):
        raise Unauthorized(
            "Mật khẩu hiện tại không đúng.",
            code="INVALID_CREDENTIALS",
            details={"field": "current_password"},
        )
    validated_new = _validate_password(new_password, "new_password")
    if verify_password(validated_new, row["password_hash"]):
        raise ValidationFailed(
            "Mật khẩu mới phải khác mật khẩu hiện tại.",
            details={"field": "new_password"},
        )
    new_hash = hash_password(validated_new)
    now_s = to_db(now)
    conn.execute(
        "UPDATE users SET password_hash = ?, must_change_password = 0, version = version + 1, "
        "updated_at = ?, updated_by = ? WHERE id = ?",
        (new_hash, now_s, row["username"], row["id"]),
    )
    _revoke_user_sessions(
        conn,
        row["id"],
        reason="PASSWORD_CHANGED",
        now=now,
        exclude_token_hash=keep_token_hash,
    )
    write_audit(
        conn,
        actor=row["username"],
        action="USER_PASSWORD_CHANGE",
        object_type="User",
        object_code=row["username"],
        at=now,
        before={"must_change_password": bool(row["must_change_password"])},
        after={"must_change_password": False},
    )
    return user_to_dict(conn, _user_row_by_username(conn, row["username"]))


# ---------- ADM-02: Quản lý tài khoản và gán/thu hồi vai trò ----------

def create_user(
    conn: sqlite3.Connection,
    *,
    username: str,
    full_name: str,
    password: str,
    roles: list[str],
    email: str | None = None,
    phone: str | None = None,
    status: str = "ACTIVE",
    must_change_password: bool = True,
    actor: str,
    now: datetime,
) -> dict:
    require_tx(conn)
    cleaned_username = _validate_username(username)
    cleaned_name = clean_text(full_name, "full_name", max_len=NAME_MAX)
    validated_password = _validate_password(password, "password")
    cleaned_roles = _validate_roles(roles)
    cleaned_email = _validate_email(email)
    cleaned_phone = _validate_phone(phone)
    if status not in USER_STATUSES:
        raise ValidationFailed("Trạng thái tài khoản không hợp lệ.", details={"field": "status"})

    existing = conn.execute("SELECT id FROM users WHERE username = ?", (cleaned_username,)).fetchone()
    if existing is not None:
        raise Conflict(
            f"Tên đăng nhập {cleaned_username} đã tồn tại.",
            code="DUPLICATE_USERNAME",
            details={"field": "username"},
        )

    now_s = to_db(now)
    cur = conn.execute(
        "INSERT INTO users(username, full_name, email, phone, password_hash, status, "
        "must_change_password, created_at, created_by, updated_at, updated_by) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            cleaned_username,
            cleaned_name,
            cleaned_email,
            cleaned_phone,
            hash_password(validated_password),
            status,
            1 if must_change_password else 0,
            now_s,
            actor,
            now_s,
            actor,
        ),
    )
    user_id = cur.lastrowid
    for role_code in cleaned_roles:
        conn.execute(
            "INSERT INTO user_roles(user_id, role_code, assigned_at, assigned_by) VALUES (?, ?, ?, ?)",
            (user_id, role_code, now_s, actor),
        )
    row = _user_row_by_username(conn, cleaned_username)
    write_audit(
        conn,
        actor=actor,
        action="USER_CREATE",
        object_type="User",
        object_code=cleaned_username,
        at=now,
        after={"full_name": cleaned_name, "status": status, "roles": cleaned_roles},
    )
    return user_to_dict(conn, row)


def get_user(conn: sqlite3.Connection, username: str) -> dict:
    return user_to_dict(conn, _user_row_by_username(conn, username))


def list_users(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    role: str | None = None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    where: list[str] = []
    params: list = []
    if status:
        where.append("u.status = ?")
        params.append(status)
    if role:
        where.append("EXISTS (SELECT 1 FROM user_roles ur WHERE ur.user_id = u.id AND ur.role_code = ?)")
        params.append(role.strip().upper())
    if q and q.strip():
        needle = f"%{q.strip()}%"
        where.append("(u.username LIKE ? OR u.full_name LIKE ? OR COALESCE(u.email, '') LIKE ?)")
        params.extend([needle, needle, needle])
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    total = conn.execute("SELECT COUNT(*) FROM users u" + clause, params).fetchone()[0]
    rows = conn.execute(
        f"SELECT u.* FROM users u{clause} ORDER BY u.username LIMIT ? OFFSET ?",
        (*params, limit, offset),
    ).fetchall()
    return {
        "items": [user_to_dict(conn, r) for r in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def update_user(
    conn: sqlite3.Connection,
    username: str,
    *,
    expected_version: int,
    full_name: str | None = None,
    email: str | None = None,
    phone: str | None = None,
    actor: str,
    now: datetime,
) -> dict:
    require_tx(conn)
    row = _user_row_by_username(conn, username)
    check_version(row["version"], expected_version)
    new_name = row["full_name"] if full_name is None else clean_text(full_name, "full_name", max_len=NAME_MAX)
    new_email = row["email"] if email is None else _validate_email(email)
    new_phone = row["phone"] if phone is None else _validate_phone(phone)
    conn.execute(
        "UPDATE users SET full_name = ?, email = ?, phone = ?, version = version + 1, "
        "updated_at = ?, updated_by = ? WHERE id = ?",
        (new_name, new_email, new_phone, to_db(now), actor, row["id"]),
    )
    updated = _user_row_by_username(conn, row["username"])
    write_audit(
        conn,
        actor=actor,
        action="USER_UPDATE",
        object_type="User",
        object_code=row["username"],
        at=now,
        before={"full_name": row["full_name"], "email": row["email"], "phone": row["phone"]},
        after={"full_name": new_name, "email": new_email, "phone": new_phone},
    )
    return user_to_dict(conn, updated)


def set_user_status(
    conn: sqlite3.Connection,
    username: str,
    *,
    expected_version: int,
    status: str,
    reason: str | None = None,
    actor: str,
    now: datetime,
) -> dict:
    require_tx(conn)
    if status not in USER_STATUSES:
        raise ValidationFailed("Trạng thái tài khoản không hợp lệ.", details={"field": "status"})
    row = _user_row_by_username(conn, username)
    check_version(row["version"], expected_version)
    cleaned_reason = clean_text(reason, "reason", max_len=NOTE_MAX, required=(status == "LOCKED")) if status == "LOCKED" else (clean_text(reason, "reason", max_len=NOTE_MAX, required=False) or None)
    current_roles = _user_roles(conn, row["id"])

    if status == "LOCKED":
        _ensure_not_last_admin(conn, row, current_roles)
        conn.execute(
            "UPDATE users SET status = 'LOCKED', lock_reason = ?, version = version + 1, "
            "updated_at = ?, updated_by = ? WHERE id = ?",
            (cleaned_reason, to_db(now), actor, row["id"]),
        )
        _revoke_user_sessions(conn, row["id"], reason="ACCOUNT_LOCKED", now=now)
    else:
        conn.execute(
            "UPDATE users SET status = 'ACTIVE', failed_login_count = 0, first_failed_at = NULL, "
            "locked_until = NULL, lock_reason = NULL, version = version + 1, "
            "updated_at = ?, updated_by = ? WHERE id = ?",
            (to_db(now), actor, row["id"]),
        )

    updated = _user_row_by_username(conn, row["username"])
    write_audit(
        conn,
        actor=actor,
        action="USER_STATUS_CHANGE",
        object_type="User",
        object_code=row["username"],
        at=now,
        before={"status": row["status"]},
        after={"status": status},
        reason=cleaned_reason,
    )
    return user_to_dict(conn, updated)


def set_user_roles(
    conn: sqlite3.Connection,
    username: str,
    *,
    expected_version: int,
    roles: list[str],
    reason: str | None = None,
    actor: str,
    now: datetime,
) -> dict:
    require_tx(conn)
    row = _user_row_by_username(conn, username)
    check_version(row["version"], expected_version)
    new_roles = _validate_roles(roles)
    old_roles = _user_roles(conn, row["id"])
    cleaned_reason = clean_text(reason, "reason", max_len=NOTE_MAX, required=False) or None

    if "ADM" in old_roles and "ADM" not in new_roles:
        _ensure_not_last_admin(conn, row, old_roles)

    now_s = to_db(now)
    conn.execute("DELETE FROM user_roles WHERE user_id = ?", (row["id"],))
    for role_code in new_roles:
        conn.execute(
            "INSERT INTO user_roles(user_id, role_code, assigned_at, assigned_by) VALUES (?, ?, ?, ?)",
            (row["id"], role_code, now_s, actor),
        )
    conn.execute(
        "UPDATE users SET version = version + 1, updated_at = ?, updated_by = ? WHERE id = ?",
        (now_s, actor, row["id"]),
    )
    updated = _user_row_by_username(conn, row["username"])
    write_audit(
        conn,
        actor=actor,
        action="USER_ROLES_CHANGE",
        object_type="User",
        object_code=row["username"],
        at=now,
        before={"roles": old_roles},
        after={"roles": new_roles},
        reason=cleaned_reason,
    )
    return user_to_dict(conn, updated)


def reset_user_password(
    conn: sqlite3.Connection,
    username: str,
    *,
    expected_version: int,
    new_password: str,
    reason: str | None = None,
    actor: str,
    now: datetime,
) -> dict:
    require_tx(conn)
    row = _user_row_by_username(conn, username)
    check_version(row["version"], expected_version)
    validated_password = _validate_password(new_password, "new_password")
    cleaned_reason = clean_text(reason, "reason", max_len=NOTE_MAX, required=False) or "Đặt lại mật khẩu bởi quản trị viên"
    now_s = to_db(now)
    conn.execute(
        "UPDATE users SET password_hash = ?, must_change_password = 1, failed_login_count = 0, "
        "first_failed_at = NULL, locked_until = NULL, version = version + 1, "
        "updated_at = ?, updated_by = ? WHERE id = ?",
        (hash_password(validated_password), now_s, actor, row["id"]),
    )
    _revoke_user_sessions(conn, row["id"], reason="PASSWORD_RESET", now=now)
    updated = _user_row_by_username(conn, row["username"])
    write_audit(
        conn,
        actor=actor,
        action="USER_PASSWORD_RESET",
        object_type="User",
        object_code=row["username"],
        at=now,
        after={"must_change_password": True},
        reason=cleaned_reason,
    )
    return user_to_dict(conn, updated)


# ---------- ADM-03: Danh mục dùng chung (Loại xe, Đơn vị, Dịch vụ & Kho) ----------

def _vtype_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "code": row["code"],
        "name": row["name"],
        "default_capacity_kg": row["default_capacity_kg"],
        "description": row["description"],
        "status": row["status"],
        "version": row["version"],
        "updated_at": row["updated_at"],
    }


def _unit_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "code": row["code"],
        "name": row["name"],
        "description": row["description"],
        "status": row["status"],
        "version": row["version"],
        "updated_at": row["updated_at"],
    }


def _service_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "code": row["code"],
        "name": row["name"],
        "description": row["description"],
        "warehouse_address": row["warehouse_address"],
        "status": row["status"],
        "version": row["version"],
        "updated_at": row["updated_at"],
    }


def _vtype_row(conn: sqlite3.Connection, code: str) -> sqlite3.Row:
    cleaned = (code or "").strip().upper()
    row = conn.execute("SELECT * FROM catalog_vehicle_types WHERE code = ?", (cleaned,)).fetchone()
    if row is None:
        raise NotFound("Không tìm thấy danh mục loại xe.", details={"code": cleaned})
    return row


def _unit_row(conn: sqlite3.Connection, code: str) -> sqlite3.Row:
    cleaned = (code or "").strip().upper()
    row = conn.execute("SELECT * FROM catalog_units WHERE code = ?", (cleaned,)).fetchone()
    if row is None:
        raise NotFound("Không tìm thấy danh mục đơn vị hàng hóa.", details={"code": cleaned})
    return row


def create_vehicle_type(
    conn: sqlite3.Connection,
    *,
    code: str,
    name: str,
    default_capacity_kg: int,
    description: str = "",
    status: str = "ACTIVE",
    actor: str,
    now: datetime,
) -> dict:
    require_tx(conn)
    cleaned_code = _validate_catalog_code(code)
    cleaned_name = clean_text(name, "name", max_len=NAME_MAX)
    if default_capacity_kg is None or default_capacity_kg <= 0:
        raise ValidationFailed("Tải trọng mặc định phải lớn hơn 0.", details={"field": "default_capacity_kg"})
    cleaned_desc = clean_text(description, "description", max_len=NOTE_MAX, required=False)
    if status not in CATALOG_STATUSES:
        raise ValidationFailed("Trạng thái danh mục không hợp lệ.", details={"field": "status"})

    if conn.execute("SELECT 1 FROM catalog_vehicle_types WHERE code = ?", (cleaned_code,)).fetchone():
        raise Conflict(
            f"Mã loại xe {cleaned_code} đã tồn tại.",
            code="DUPLICATE_CODE",
            details={"field": "code"},
        )
    now_s = to_db(now)
    conn.execute(
        "INSERT INTO catalog_vehicle_types(code, name, default_capacity_kg, description, status, "
        "created_at, created_by, updated_at, updated_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (cleaned_code, cleaned_name, default_capacity_kg, cleaned_desc, status, now_s, actor, now_s, actor),
    )
    row = _vtype_row(conn, cleaned_code)
    write_audit(
        conn,
        actor=actor,
        action="CATALOG_VEHICLE_TYPE_CREATE",
        object_type="VehicleType",
        object_code=cleaned_code,
        at=now,
        after=_vtype_to_dict(row),
    )
    return _vtype_to_dict(row)


def update_vehicle_type(
    conn: sqlite3.Connection,
    code: str,
    *,
    expected_version: int,
    name: str | None = None,
    default_capacity_kg: int | None = None,
    description: str | None = None,
    actor: str,
    now: datetime,
) -> dict:
    require_tx(conn)
    row = _vtype_row(conn, code)
    check_version(row["version"], expected_version)
    new_name = row["name"] if name is None else clean_text(name, "name", max_len=NAME_MAX)
    if default_capacity_kg is not None and default_capacity_kg <= 0:
        raise ValidationFailed("Tải trọng mặc định phải lớn hơn 0.", details={"field": "default_capacity_kg"})
    new_cap = row["default_capacity_kg"] if default_capacity_kg is None else default_capacity_kg
    new_desc = row["description"] if description is None else clean_text(description, "description", max_len=NOTE_MAX, required=False)

    conn.execute(
        "UPDATE catalog_vehicle_types SET name = ?, default_capacity_kg = ?, description = ?, "
        "version = version + 1, updated_at = ?, updated_by = ? WHERE id = ?",
        (new_name, new_cap, new_desc, to_db(now), actor, row["id"]),
    )
    updated = _vtype_row(conn, row["code"])
    write_audit(
        conn,
        actor=actor,
        action="CATALOG_VEHICLE_TYPE_UPDATE",
        object_type="VehicleType",
        object_code=row["code"],
        at=now,
        before={"name": row["name"], "default_capacity_kg": row["default_capacity_kg"], "description": row["description"]},
        after={"name": new_name, "default_capacity_kg": new_cap, "description": new_desc},
    )
    return _vtype_to_dict(updated)


def set_vehicle_type_status(
    conn: sqlite3.Connection,
    code: str,
    *,
    expected_version: int,
    status: str,
    reason: str | None = None,
    actor: str,
    now: datetime,
) -> dict:
    require_tx(conn)
    if status not in CATALOG_STATUSES:
        raise ValidationFailed("Trạng thái danh mục không hợp lệ.", details={"field": "status"})
    row = _vtype_row(conn, code)
    check_version(row["version"], expected_version)
    cleaned_reason = clean_text(reason, "reason", max_len=NOTE_MAX, required=False) or None
    conn.execute(
        "UPDATE catalog_vehicle_types SET status = ?, version = version + 1, updated_at = ?, updated_by = ? WHERE id = ?",
        (status, to_db(now), actor, row["id"]),
    )
    updated = _vtype_row(conn, row["code"])
    write_audit(
        conn,
        actor=actor,
        action="CATALOG_VEHICLE_TYPE_STATUS",
        object_type="VehicleType",
        object_code=row["code"],
        at=now,
        before={"status": row["status"]},
        after={"status": status},
        reason=cleaned_reason,
    )
    return _vtype_to_dict(updated)


def list_vehicle_types(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    where, params = [], []
    if status:
        where.append("status = ?")
        params.append(status)
    if q and q.strip():
        needle = f"%{q.strip()}%"
        where.append("(code LIKE ? OR name LIKE ?)")
        params.extend([needle.upper(), needle])
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    total = conn.execute("SELECT COUNT(*) FROM catalog_vehicle_types" + clause, params).fetchone()[0]
    rows = conn.execute(
        f"SELECT * FROM catalog_vehicle_types{clause} ORDER BY code LIMIT ? OFFSET ?",
        (*params, limit, offset),
    ).fetchall()
    return {"items": [_vtype_to_dict(r) for r in rows], "total": total, "limit": limit, "offset": offset}


def create_unit(
    conn: sqlite3.Connection,
    *,
    code: str,
    name: str,
    description: str = "",
    status: str = "ACTIVE",
    actor: str,
    now: datetime,
) -> dict:
    require_tx(conn)
    cleaned_code = _validate_catalog_code(code)
    cleaned_name = clean_text(name, "name", max_len=NAME_MAX)
    cleaned_desc = clean_text(description, "description", max_len=NOTE_MAX, required=False)
    if status not in CATALOG_STATUSES:
        raise ValidationFailed("Trạng thái danh mục không hợp lệ.", details={"field": "status"})
    if conn.execute("SELECT 1 FROM catalog_units WHERE code = ?", (cleaned_code,)).fetchone():
        raise Conflict(
            f"Mã đơn vị {cleaned_code} đã tồn tại.",
            code="DUPLICATE_CODE",
            details={"field": "code"},
        )
    now_s = to_db(now)
    conn.execute(
        "INSERT INTO catalog_units(code, name, description, status, created_at, created_by, updated_at, updated_by) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (cleaned_code, cleaned_name, cleaned_desc, status, now_s, actor, now_s, actor),
    )
    row = _unit_row(conn, cleaned_code)
    write_audit(
        conn,
        actor=actor,
        action="CATALOG_UNIT_CREATE",
        object_type="Unit",
        object_code=cleaned_code,
        at=now,
        after=_unit_to_dict(row),
    )
    return _unit_to_dict(row)


def update_unit(
    conn: sqlite3.Connection,
    code: str,
    *,
    expected_version: int,
    name: str | None = None,
    description: str | None = None,
    actor: str,
    now: datetime,
) -> dict:
    require_tx(conn)
    row = _unit_row(conn, code)
    check_version(row["version"], expected_version)
    new_name = row["name"] if name is None else clean_text(name, "name", max_len=NAME_MAX)
    new_desc = row["description"] if description is None else clean_text(description, "description", max_len=NOTE_MAX, required=False)
    conn.execute(
        "UPDATE catalog_units SET name = ?, description = ?, version = version + 1, updated_at = ?, updated_by = ? "
        "WHERE id = ?",
        (new_name, new_desc, to_db(now), actor, row["id"]),
    )
    updated = _unit_row(conn, row["code"])
    write_audit(
        conn,
        actor=actor,
        action="CATALOG_UNIT_UPDATE",
        object_type="Unit",
        object_code=row["code"],
        at=now,
        before={"name": row["name"], "description": row["description"]},
        after={"name": new_name, "description": new_desc},
    )
    return _unit_to_dict(updated)


def set_unit_status(
    conn: sqlite3.Connection,
    code: str,
    *,
    expected_version: int,
    status: str,
    reason: str | None = None,
    actor: str,
    now: datetime,
) -> dict:
    require_tx(conn)
    if status not in CATALOG_STATUSES:
        raise ValidationFailed("Trạng thái danh mục không hợp lệ.", details={"field": "status"})
    row = _unit_row(conn, code)
    check_version(row["version"], expected_version)
    cleaned_reason = clean_text(reason, "reason", max_len=NOTE_MAX, required=False) or None
    conn.execute(
        "UPDATE catalog_units SET status = ?, version = version + 1, updated_at = ?, updated_by = ? WHERE id = ?",
        (status, to_db(now), actor, row["id"]),
    )
    updated = _unit_row(conn, row["code"])
    write_audit(
        conn,
        actor=actor,
        action="CATALOG_UNIT_STATUS",
        object_type="Unit",
        object_code=row["code"],
        at=now,
        before={"status": row["status"]},
        after={"status": status},
        reason=cleaned_reason,
    )
    return _unit_to_dict(updated)


def list_units(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    where, params = [], []
    if status:
        where.append("status = ?")
        params.append(status)
    if q and q.strip():
        needle = f"%{q.strip()}%"
        where.append("(code LIKE ? OR name LIKE ?)")
        params.extend([needle.upper(), needle])
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    total = conn.execute("SELECT COUNT(*) FROM catalog_units" + clause, params).fetchone()[0]
    rows = conn.execute(
        f"SELECT * FROM catalog_units{clause} ORDER BY code LIMIT ? OFFSET ?",
        (*params, limit, offset),
    ).fetchall()
    return {"items": [_unit_to_dict(r) for r in rows], "total": total, "limit": limit, "offset": offset}


def upsert_service_catalog(
    conn: sqlite3.Connection,
    code: str,
    *,
    name: str | None = None,
    description: str | None = None,
    warehouse_address: str | None = None,
    status: str | None = None,
    expected_version: int | None = None,
    actor: str,
    now: datetime,
) -> dict:
    require_tx(conn)
    cleaned_code = (code or "").strip().upper()
    if cleaned_code not in SERVICE_CODES:
        raise ValidationFailed("Mã dịch vụ phải là TRANSPORT, RENTAL hoặc WAREHOUSE.", details={"field": "code"})
    if status is not None and status not in CATALOG_STATUSES:
        raise ValidationFailed("Trạng thái dịch vụ không hợp lệ.", details={"field": "status"})

    row = conn.execute("SELECT * FROM catalog_services WHERE code = ?", (cleaned_code,)).fetchone()
    now_s = to_db(now)
    if row is None:
        cleaned_name = clean_text(name or cleaned_code, "name", max_len=NAME_MAX)
        cleaned_desc = clean_text(description or "", "description", max_len=NOTE_MAX, required=False)
        cleaned_addr = clean_text(warehouse_address or "", "warehouse_address", max_len=NOTE_MAX, required=False)
        final_status = status or "ACTIVE"
        conn.execute(
            "INSERT INTO catalog_services(code, name, description, warehouse_address, status, "
            "created_at, created_by, updated_at, updated_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (cleaned_code, cleaned_name, cleaned_desc, cleaned_addr, final_status, now_s, actor, now_s, actor),
        )
        created = conn.execute("SELECT * FROM catalog_services WHERE code = ?", (cleaned_code,)).fetchone()
        write_audit(
            conn,
            actor=actor,
            action="CATALOG_SERVICE_CREATE",
            object_type="ServiceCatalog",
            object_code=cleaned_code,
            at=now,
            after=_service_to_dict(created),
        )
        return _service_to_dict(created)

    if expected_version is not None:
        check_version(row["version"], expected_version)
    new_name = row["name"] if name is None else clean_text(name, "name", max_len=NAME_MAX)
    new_desc = row["description"] if description is None else clean_text(description, "description", max_len=NOTE_MAX, required=False)
    new_addr = row["warehouse_address"] if warehouse_address is None else clean_text(warehouse_address, "warehouse_address", max_len=NOTE_MAX, required=False)
    new_status = row["status"] if status is None else status
    conn.execute(
        "UPDATE catalog_services SET name = ?, description = ?, warehouse_address = ?, status = ?, "
        "version = version + 1, updated_at = ?, updated_by = ? WHERE id = ?",
        (new_name, new_desc, new_addr, new_status, now_s, actor, row["id"]),
    )
    updated = conn.execute("SELECT * FROM catalog_services WHERE id = ?", (row["id"],)).fetchone()
    write_audit(
        conn,
        actor=actor,
        action="CATALOG_SERVICE_UPDATE",
        object_type="ServiceCatalog",
        object_code=cleaned_code,
        at=now,
        before=_service_to_dict(row),
        after=_service_to_dict(updated),
    )
    return _service_to_dict(updated)


def list_service_catalogs(conn: sqlite3.Connection, *, status: str | None = None) -> dict:
    if status:
        rows = conn.execute(
            "SELECT * FROM catalog_services WHERE status = ? ORDER BY code", (status,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM catalog_services ORDER BY code").fetchall()
    return {"items": [_service_to_dict(r) for r in rows], "total": len(rows)}
