"""Tệp bằng chứng tối thiểu (NFR-07). BE3 sở hữu dịch vụ tệp và có thể thay module này.

- Chỉ nhận PDF/JPG/PNG, tối đa 5 MB. Loại tệp xác định bằng nội dung, không tin đuôi tệp.
- Lưu bằng tên ngẫu nhiên, không dùng tên người dùng gửi; không bao giờ thực thi.
- Tệp tải lên trước, sau đó gắn vào hồ sơ (giao hàng, bàn giao xe...). Đã gắn thì không gắn lại.
"""

import hashlib
import re
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

from .audit import write_audit
from .db import next_code, require_tx
from .errors import Conflict, NotFound, ValidationFailed
from .timeutil import to_db

MAX_BYTES = 5 * 1024 * 1024
_SIGNATURES = (
    (b"%PDF-", "application/pdf", ".pdf"),
    (b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
    (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
)
_ALLOWED_EXT = {".pdf", ".png", ".jpg", ".jpeg"}
_UNSAFE_NAME = re.compile(r"[^\w.\- ()]+", re.UNICODE)


def _sniff(data: bytes) -> tuple[str, str] | None:
    for magic, content_type, ext in _SIGNATURES:
        if data.startswith(magic):
            return content_type, ext
    return None


def _safe_name(filename: str) -> str:
    name = Path(filename or "tep").name
    name = _UNSAFE_NAME.sub("_", name).strip(" .") or "tep"
    return name[:200]


def attachment_to_dict(row: sqlite3.Row) -> dict:
    return {
        "code": row["code"],
        "original_name": row["original_name"],
        "content_type": row["content_type"],
        "size_bytes": row["size_bytes"],
        "owner_type": row["owner_type"],
        "owner_code": row["owner_code"],
        "uploaded_at": row["uploaded_at"],
        "uploaded_by": row["uploaded_by"],
    }


def store(
    conn: sqlite3.Connection, files_dir: str, *, filename: str, data: bytes, actor: str, now: datetime
) -> dict:
    require_tx(conn)
    if not data:
        raise ValidationFailed("Tệp rỗng.", details={"field": "file"})
    if len(data) > MAX_BYTES:
        raise ValidationFailed("Tệp vượt quá 5 MB.", details={"field": "file"})
    sniffed = _sniff(data)
    ext = Path(filename or "").suffix.lower()
    if sniffed is None or ext not in _ALLOWED_EXT:
        raise ValidationFailed("Chỉ nhận tệp PDF, JPG hoặc PNG.", details={"field": "file"})
    content_type, stored_ext = sniffed
    stored_name = uuid.uuid4().hex + stored_ext
    folder = Path(files_dir)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / stored_name).write_bytes(data)
    code = next_code(conn, "ATT")
    conn.execute(
        "INSERT INTO attachments(code, original_name, stored_name, content_type, size_bytes, sha256, "
        "uploaded_at, uploaded_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (code, _safe_name(filename), stored_name, content_type, len(data),
         hashlib.sha256(data).hexdigest(), to_db(now), actor),
    )
    write_audit(conn, actor=actor, action="ATTACHMENT_UPLOAD", object_type="Attachment", object_code=code,
                at=now, after={"content_type": content_type, "size_bytes": len(data)})
    return attachment_to_dict(conn.execute("SELECT * FROM attachments WHERE code = ?", (code,)).fetchone())


def link(
    conn: sqlite3.Connection, codes: list[str] | None, *, owner_type: str, owner_code: str, actor: str,
    now: datetime, min_count: int = 1,
) -> list[dict]:
    """Gắn các tệp đã tải lên vào một hồ sơ. Thiếu số tệp tối thiểu thì từ chối."""
    require_tx(conn)
    unique = list(dict.fromkeys(c.strip() for c in (codes or []) if c and c.strip()))
    if len(unique) < min_count:
        raise ValidationFailed(f"Cần ít nhất {min_count} tệp bằng chứng PDF/JPG/PNG.",
                               details={"field": "attachment_codes"})
    linked = []
    for code in unique:
        row = conn.execute("SELECT * FROM attachments WHERE code = ?", (code,)).fetchone()
        if row is None:
            raise NotFound(f"Không tìm thấy tệp {code}.", details={"field": "attachment_codes"})
        if row["owner_code"] is not None:
            raise Conflict(f"Tệp {code} đã được dùng cho hồ sơ khác.", code="ATTACHMENT_ALREADY_USED",
                           details={"field": "attachment_codes"})
        conn.execute(
            "UPDATE attachments SET owner_type = ?, owner_code = ?, linked_at = ?, linked_by = ? WHERE id = ?",
            (owner_type, owner_code, to_db(now), actor, row["id"]),
        )
        linked.append(attachment_to_dict(conn.execute("SELECT * FROM attachments WHERE id = ?",
                                                      (row["id"],)).fetchone()))
    return linked


def list_for(conn: sqlite3.Connection, owner_type: str, owner_code: str) -> list[dict]:
    rows = conn.execute("SELECT * FROM attachments WHERE owner_type = ? AND owner_code = ? ORDER BY id",
                        (owner_type, owner_code)).fetchall()
    return [attachment_to_dict(r) for r in rows]


def open_file(conn: sqlite3.Connection, files_dir: str, code: str) -> tuple[dict, Path]:
    row = conn.execute("SELECT * FROM attachments WHERE code = ?", (code,)).fetchone()
    if row is None:
        raise NotFound("Không tìm thấy tệp.")
    path = Path(files_dir) / row["stored_name"]
    if not path.is_file():
        raise NotFound("Tệp không còn trong kho lưu trữ.")
    return attachment_to_dict(row), path
