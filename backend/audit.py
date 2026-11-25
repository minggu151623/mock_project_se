"""Dịch vụ Audit dùng chung theo BR-17 và ADM-04 (BE3).

- Mọi thay đổi quan trọng được ghi trong cùng transaction (`require_tx(conn)`).
- Bảng `audit_log` chỉ cho thêm (append-only), được bảo vệ bởi trigger `trg_audit_no_update`
  và `trg_audit_no_delete` trong `schema.sql`, không có bất kỳ hàm hay API sửa/xóa nào.
- Tự động che (`[REDACTED]`) mọi trường nhạy cảm như mật khẩu hay token tra cứu nếu vô tình
  được truyền vào `before` hoặc `after`.
"""

import json
import sqlite3
from datetime import datetime
from typing import Any

from .db import require_tx
from .errors import NotFound
from .timeutil import to_db

_SENSITIVE_KEYS = frozenset({
    "password",
    "password_hash",
    "current_password",
    "new_password",
    "temporary_password",
    "token",
    "lookup_token",
    "session_token",
    "token_hash",
    "public_token_hash",
})


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: ("[REDACTED]" if str(k).lower() in _SENSITIVE_KEYS else _sanitize(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    return value


def write_audit(
    conn: sqlite3.Connection,
    *,
    actor: str,
    action: str,
    object_type: str,
    object_code: str,
    at: datetime,
    before: dict | None = None,
    after: dict | None = None,
    reason: str | None = None,
) -> None:
    require_tx(conn)
    safe_before = None if before is None else _sanitize(before)
    safe_after = None if after is None else _sanitize(after)
    conn.execute(
        "INSERT INTO audit_log(occurred_at, actor, action, object_type, object_code, "
        "before_json, after_json, reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            to_db(at),
            actor,
            action,
            object_type,
            object_code,
            None if safe_before is None else json.dumps(safe_before, ensure_ascii=False, sort_keys=True, default=str),
            None if safe_after is None else json.dumps(safe_after, ensure_ascii=False, sort_keys=True, default=str),
            reason,
        ),
    )


def audit_row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "occurred_at": row["occurred_at"],
        "actor": row["actor"],
        "action": row["action"],
        "object_type": row["object_type"],
        "object_code": row["object_code"],
        "before": json.loads(row["before_json"]) if row["before_json"] else None,
        "after": json.loads(row["after_json"]) if row["after_json"] else None,
        "reason": row["reason"],
    }


def get_audit_log(conn: sqlite3.Connection, audit_id: int) -> dict:
    row = conn.execute("SELECT * FROM audit_log WHERE id = ?", (audit_id,)).fetchone()
    if row is None:
        raise NotFound("Không tìm thấy bản ghi audit.")
    return audit_row_to_dict(row)


def list_audit_logs(
    conn: sqlite3.Connection,
    *,
    object_type: str | None = None,
    object_code: str | None = None,
    actor: str | None = None,
    action: str | None = None,
    from_at: datetime | None = None,
    to_at: datetime | None = None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    where: list[str] = []
    params: list[Any] = []
    if object_type and object_type.strip():
        where.append("object_type = ?")
        params.append(object_type.strip())
    if object_code and object_code.strip():
        where.append("object_code = ?")
        params.append(object_code.strip())
    if actor and actor.strip():
        where.append("actor = ?")
        params.append(actor.strip())
    if action and action.strip():
        where.append("action = ?")
        params.append(action.strip())
    if from_at is not None:
        where.append("occurred_at >= ?")
        params.append(to_db(from_at))
    if to_at is not None:
        where.append("occurred_at <= ?")
        params.append(to_db(to_at))
    if q and q.strip():
        needle = f"%{q.strip()}%"
        where.append(
            "(object_code LIKE ? OR actor LIKE ? OR action LIKE ? OR object_type LIKE ? "
            "OR COALESCE(reason, '') LIKE ?)"
        )
        params.extend([needle] * 5)

    clause = (" WHERE " + " AND ".join(where)) if where else ""
    total = conn.execute("SELECT COUNT(*) FROM audit_log" + clause, params).fetchone()[0]
    rows = conn.execute(
        f"SELECT * FROM audit_log{clause} ORDER BY occurred_at DESC, id DESC LIMIT ? OFFSET ?",
        (*params, limit, offset),
    ).fetchall()
    return {
        "items": [audit_row_to_dict(r) for r in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }
