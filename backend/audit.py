"""Ghi audit tối thiểu theo BR-17. BE3 sở hữu phần audit và sẽ thay helper này."""

import json
import sqlite3
from datetime import datetime

from .db import require_tx
from .timeutil import to_db


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
    conn.execute(
        "INSERT INTO audit_log(occurred_at, actor, action, object_type, object_code, "
        "before_json, after_json, reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            to_db(at),
            actor,
            action,
            object_type,
            object_code,
            None if before is None else json.dumps(before, ensure_ascii=False, sort_keys=True),
            None if after is None else json.dumps(after, ensure_ascii=False, sort_keys=True),
            reason,
        ),
    )
