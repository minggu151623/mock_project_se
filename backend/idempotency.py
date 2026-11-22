"""Chống gửi trùng (NFR-05). BE1 sở hữu helper này; BE2 dùng tạm bản tối thiểu.

Gửi lại cùng khóa và cùng nội dung trả lại đúng kết quả lần đầu. Dùng lại khóa cho
nội dung khác bị từ chối. Lỗi nghiệp vụ không được lưu, vì transaction bị hủy.
"""

import hashlib
import json
import sqlite3
from collections.abc import Callable
from datetime import datetime

from .db import require_tx
from .errors import Conflict, ValidationFailed
from .timeutil import to_db

MAX_KEY_LENGTH = 100


def _hash(operation: str, payload: dict) -> str:
    raw = json.dumps({"op": operation, "payload": payload}, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def run_idempotent(
    conn: sqlite3.Connection,
    *,
    key: str | None,
    actor: str,
    operation: str,
    payload: dict,
    now: datetime,
    action: Callable[[], tuple[int, dict]],
) -> tuple[int, dict]:
    require_tx(conn)
    if key is None:
        return action()
    key = key.strip()
    if not key or len(key) > MAX_KEY_LENGTH:
        raise ValidationFailed(
            f"Idempotency-Key phải có 1–{MAX_KEY_LENGTH} ký tự.",
            details={"field": "Idempotency-Key"},
        )
    request_hash = _hash(operation, payload)
    row = conn.execute(
        "SELECT operation, request_hash, status_code, response_json FROM idempotency_keys "
        "WHERE actor = ? AND key = ?",
        (actor, key),
    ).fetchone()
    if row is not None:
        if row["operation"] != operation or row["request_hash"] != request_hash:
            raise Conflict(
                "Idempotency-Key này đã được dùng cho một thao tác khác.",
                code="IDEMPOTENCY_KEY_REUSED",
            )
        return row["status_code"], json.loads(row["response_json"])
    status_code, body = action()
    conn.execute(
        "INSERT INTO idempotency_keys(actor, key, operation, request_hash, status_code, "
        "response_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (actor, key, operation, request_hash, status_code,
         json.dumps(body, ensure_ascii=False, default=str), to_db(now)),
    )
    return status_code, body
