"""Nghiệp vụ kênh tiếp nhận khách hàng: website công khai, gửi yêu cầu dịch vụ,
cấp token tra cứu >= 128 bit, tra cứu tiến độ công khai theo BR-18 và quản trị CMS (PUB-01→05 - BE3).
"""

import hashlib
import hmac
import html
import json
import re
import secrets
import sqlite3
from datetime import datetime, timedelta
from html.parser import HTMLParser
from typing import Any

from . import sales
from .audit import write_audit
from .db import next_code, require_tx, write_tx
from .errors import Conflict, DomainError, NotFound, Unauthorized, ValidationFailed
from .timeutil import to_db, to_utc
from .validation import NAME_MAX, NOTE_MAX, check_version, clean_text

CONTENT_TYPES = ("COMPANY", "SERVICE", "FAQ", "CONTACT")
SERVICE_TYPES = ("TRANSPORT", "RENTAL", "WAREHOUSE")
CMS_STATUSES = ("DRAFT", "PUBLISHED", "HIDDEN")

SUBMIT_RATE_LIMIT = 5
SUBMIT_RATE_WINDOW = timedelta(minutes=10)
LOOKUP_FAIL_LIMIT = 10
LOOKUP_FAIL_WINDOW = timedelta(minutes=15)

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9\-]{1,99}$")
_SCRIPT_DETECT_RE = re.compile(
    r"(<\s*/?\s*(script|iframe|object|embed|svg|form|meta|link)\b|"
    r"javascript\s*:|vbscript\s*:|data\s*:\s*text/html|"
    r"\bon[a-z]{2,20}\s*=)",
    re.IGNORECASE,
)

_ALLOWED_HTML_TAGS = frozenset({
    "p", "br", "h1", "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "li", "strong", "b", "em", "i", "u",
    "blockquote", "code", "pre", "span", "div", "section", "article",
    "table", "thead", "tbody", "tr", "th", "td", "a",
})
_VOID_HTML_TAGS = frozenset({"br"})
_SAFE_HREF_PREFIXES = ("http://", "https://", "mailto:", "tel:", "/", "#")

PUBLIC_SUPPORT_CONTACT = {
    "company_name": "Công ty Vận tải & Kho vận Trung chuyển",
    "hotline": "1900-6868",
    "email": "hotro@vantai-mock.vn",
    "working_hours": "08:00 - 17:30 (Thứ 2 - Thứ 7)",
}


class RateLimited(DomainError):
    status_code = 429
    default_code = "RATE_LIMITED"


class _SafeHTMLSanitizer(HTMLParser):
    """Làm sạch HTML cho CMS (PUB-05): loại bỏ hoàn toàn thẻ script/iframe/object và thuộc tính on*."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        t = tag.lower()
        if t in ("script", "style", "iframe", "object", "embed", "svg"):
            self._skip_depth += 1
            return
        if self._skip_depth > 0 or t not in _ALLOWED_HTML_TAGS:
            return
        safe_attrs: list[str] = []
        for k, v in attrs:
            attr_name = k.lower()
            if attr_name.startswith("on"):
                continue
            val = (v or "").strip()
            if t == "a" and attr_name == "href":
                lower_val = val.lower()
                if any(lower_val.startswith(prefix) for prefix in _SAFE_HREF_PREFIXES):
                    safe_attrs.append(f' href="{html.escape(val, quote=True)}" rel="noopener noreferrer"')
            elif attr_name in ("title", "class") and val and not _SCRIPT_DETECT_RE.search(val):
                safe_attrs.append(f' {attr_name}="{html.escape(val, quote=True)}"')
        self.parts.append(f"<{t}{''.join(safe_attrs)}>")

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t in ("script", "style", "iframe", "object", "embed", "svg"):
            if self._skip_depth > 0:
                self._skip_depth -= 1
            return
        if self._skip_depth > 0 or t not in _ALLOWED_HTML_TAGS or t in _VOID_HTML_TAGS:
            return
        self.parts.append(f"</{t}>")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0 and data:
            self.parts.append(html.escape(data, quote=False))


def sanitize_html(raw_html: str | None, field: str = "body_html", *, max_len: int = 50_000) -> str:
    text = (raw_html or "").strip()
    if not text:
        raise ValidationFailed(f"Trường {field} là bắt buộc.", details={"field": field})
    if len(text) > max_len:
        raise ValidationFailed(f"Trường {field} tối đa {max_len} ký tự.", details={"field": field})
    parser = _SafeHTMLSanitizer()
    parser.feed(text)
    parser.close()
    cleaned = "".join(parser.parts).strip()
    if not cleaned:
        raise ValidationFailed(
            f"Trường {field} không còn nội dung hợp lệ sau khi làm sạch mã HTML.",
            details={"field": field},
        )
    return cleaned


def assert_no_script_payload(value: Any, field_path: str = "body") -> None:
    """Chặn mã script/XSS trong dữ liệu form khách gửi (PUB-02, NEG-01/bảo mật)."""
    if isinstance(value, str):
        if _SCRIPT_DETECT_RE.search(value) or "<" in value and ">" in value and re.search(r"<\s*[a-z!/]", value, re.I):
            raise ValidationFailed(
                "Nội dung không được chứa thẻ HTML hoặc mã kịch bản (script).",
                details={"field": field_path},
            )
    elif isinstance(value, dict):
        for k, v in value.items():
            sub_field = str(k) if field_path in ("body", "contact", "service_data") else f"{field_path}.{k}"
            assert_no_script_payload(v, sub_field)
    elif isinstance(value, list):
        for idx, item in enumerate(value):
            assert_no_script_payload(item, f"{field_path}.{idx}")


def _validate_slug(slug: str | None) -> str:
    cleaned = (slug or "").strip().lower()
    if not _SLUG_RE.match(cleaned):
        raise ValidationFailed(
            "Slug gồm 2–100 ký tự chữ thường, số hoặc dấu gạch ngang.",
            details={"field": "slug"},
        )
    return cleaned


def _content_to_dict(row: sqlite3.Row, *, public_view: bool = False) -> dict:
    meta = json.loads(row["meta_json"]) if row["meta_json"] else {}
    data = {
        "code": row["code"],
        "slug": row["slug"],
        "content_type": row["content_type"],
        "service_type": row["service_type"],
        "title": row["title"],
        "summary": row["summary"],
        "body_html": row["body_html"],
        "meta": meta,
        "sort_order": row["sort_order"],
        "published_at": row["published_at"],
        "updated_at": row["updated_at"],
    }
    if not public_view:
        data.update({
            "status": row["status"],
            "published_by": row["published_by"],
            "version": row["version"],
            "created_at": row["created_at"],
            "created_by": row["created_by"],
            "updated_by": row["updated_by"],
        })
    return data


def _content_row_by_code(conn: sqlite3.Connection, code: str) -> sqlite3.Row:
    cleaned = (code or "").strip().upper()
    row = conn.execute("SELECT * FROM public_contents WHERE code = ?", (cleaned,)).fetchone()
    if row is None:
        raise NotFound("Không tìm thấy bài viết CMS.", details={"code": cleaned})
    return row


def _record_revision(
    conn: sqlite3.Connection, row: sqlite3.Row, *, actor: str, now: datetime, reason: str | None = None
) -> None:
    conn.execute(
        "INSERT INTO public_content_revisions(content_code, version_no, title, summary, body_html, "
        "status, changed_at, changed_by, reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            row["code"],
            row["version"],
            row["title"],
            row["summary"],
            row["body_html"],
            row["status"],
            to_db(now),
            actor,
            reason,
        ),
    )


# ---------- PUB-01: Đọc nội dung công khai (chỉ PUBLISHED) ----------

def list_published_contents(
    conn: sqlite3.Connection,
    *,
    content_type: str | None = None,
    service_type: str | None = None,
) -> dict:
    where = ["status = 'PUBLISHED'"]
    params: list[Any] = []
    if content_type:
        where.append("content_type = ?")
        params.append(content_type.strip().upper())
    if service_type:
        where.append("service_type = ?")
        params.append(service_type.strip().upper())
    sql = (
        "SELECT * FROM public_contents WHERE "
        + " AND ".join(where)
        + " ORDER BY sort_order ASC, id ASC"
    )
    rows = conn.execute(sql, params).fetchall()
    return {
        "items": [_content_to_dict(r, public_view=True) for r in rows],
        "total": len(rows),
        "support_contact": PUBLIC_SUPPORT_CONTACT,
    }


def get_published_content_by_slug(conn: sqlite3.Connection, slug: str) -> dict:
    cleaned = (slug or "").strip().lower()
    row = conn.execute(
        "SELECT * FROM public_contents WHERE slug = ? AND status = 'PUBLISHED'",
        (cleaned,),
    ).fetchone()
    if row is None:
        raise NotFound("Không tìm thấy nội dung công khai.", details={"slug": cleaned})
    return _content_to_dict(row, public_view=True)


def get_public_catalogs(conn: sqlite3.Connection) -> dict:
    vehicle_types = [
        {
            "code": r["code"],
            "name": r["name"],
            "default_capacity_kg": r["default_capacity_kg"],
            "description": r["description"],
        }
        for r in conn.execute(
            "SELECT code, name, default_capacity_kg, description FROM catalog_vehicle_types "
            "WHERE status = 'ACTIVE' ORDER BY default_capacity_kg, code"
        ).fetchall()
    ]
    units = [
        {"code": r["code"], "name": r["name"], "description": r["description"]}
        for r in conn.execute(
            "SELECT code, name, description FROM catalog_units WHERE status = 'ACTIVE' ORDER BY code"
        ).fetchall()
    ]
    services = [
        {
            "code": r["code"],
            "name": r["name"],
            "description": r["description"],
            "warehouse_address": r["warehouse_address"],
        }
        for r in conn.execute(
            "SELECT code, name, description, warehouse_address FROM catalog_services "
            "WHERE status = 'ACTIVE' ORDER BY code"
        ).fetchall()
    ]
    return {"vehicle_types": vehicle_types, "units": units, "services": services}


# ---------- PUB-05: Quản lý nội dung công khai (CMS) ----------

def create_cms_content(
    conn: sqlite3.Connection,
    *,
    slug: str,
    content_type: str,
    title: str,
    body_html: str,
    summary: str = "",
    service_type: str | None = None,
    meta: dict | None = None,
    sort_order: int = 0,
    status: str = "DRAFT",
    actor: str,
    now: datetime,
) -> dict:
    require_tx(conn)
    cleaned_slug = _validate_slug(slug)
    cleaned_type = (content_type or "").strip().upper()
    if cleaned_type not in CONTENT_TYPES:
        raise ValidationFailed("Loại nội dung CMS không hợp lệ.", details={"field": "content_type"})
    if cleaned_type == "SERVICE":
        cleaned_service = (service_type or "").strip().upper()
        if cleaned_service not in SERVICE_TYPES:
            raise ValidationFailed(
                "Trang dịch vụ cần chỉ rõ service_type là TRANSPORT, RENTAL hoặc WAREHOUSE.",
                details={"field": "service_type"},
            )
    else:
        if service_type is not None and str(service_type).strip():
            raise ValidationFailed(
                "Chỉ trang loại SERVICE mới có service_type.",
                details={"field": "service_type"},
            )
        cleaned_service = None

    if status not in CMS_STATUSES:
        raise ValidationFailed("Trạng thái CMS không hợp lệ.", details={"field": "status"})

    cleaned_title = clean_text(title, "title", max_len=NAME_MAX)
    assert_no_script_payload(cleaned_title, "title")
    cleaned_summary = clean_text(summary, "summary", max_len=NOTE_MAX, required=False)
    assert_no_script_payload(cleaned_summary, "summary")
    cleaned_html = sanitize_html(body_html, "body_html")

    if conn.execute("SELECT 1 FROM public_contents WHERE slug = ?", (cleaned_slug,)).fetchone():
        raise Conflict(
            f"Slug '{cleaned_slug}' đã tồn tại.",
            code="DUPLICATE_SLUG",
            details={"field": "slug"},
        )

    code = next_code(conn, "CMS")
    now_s = to_db(now)
    published_at = now_s if status == "PUBLISHED" else None
    published_by = actor if status == "PUBLISHED" else None
    meta_json = json.dumps(meta or {}, ensure_ascii=False, sort_keys=True)

    conn.execute(
        "INSERT INTO public_contents(code, slug, content_type, service_type, title, summary, body_html, "
        "meta_json, sort_order, status, published_at, published_by, created_at, created_by, updated_at, updated_by) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            code,
            cleaned_slug,
            cleaned_type,
            cleaned_service,
            cleaned_title,
            cleaned_summary,
            cleaned_html,
            meta_json,
            int(sort_order),
            status,
            published_at,
            published_by,
            now_s,
            actor,
            now_s,
            actor,
        ),
    )
    row = _content_row_by_code(conn, code)
    _record_revision(conn, row, actor=actor, now=now, reason="Khởi tạo nội dung")
    write_audit(
        conn,
        actor=actor,
        action="CMS_CONTENT_CREATE",
        object_type="PublicContent",
        object_code=code,
        at=now,
        after={"slug": cleaned_slug, "content_type": cleaned_type, "status": status},
    )
    return get_cms_content(conn, code)


def update_cms_content(
    conn: sqlite3.Connection,
    code: str,
    *,
    expected_version: int,
    title: str | None = None,
    summary: str | None = None,
    body_html: str | None = None,
    meta: dict | None = None,
    sort_order: int | None = None,
    reason: str | None = None,
    actor: str,
    now: datetime,
) -> dict:
    require_tx(conn)
    row = _content_row_by_code(conn, code)
    check_version(row["version"], expected_version)

    new_title = row["title"] if title is None else clean_text(title, "title", max_len=NAME_MAX)
    assert_no_script_payload(new_title, "title")
    new_summary = row["summary"] if summary is None else clean_text(summary, "summary", max_len=NOTE_MAX, required=False)
    assert_no_script_payload(new_summary, "summary")
    new_html = row["body_html"] if body_html is None else sanitize_html(body_html, "body_html")
    new_meta = row["meta_json"] if meta is None else json.dumps(meta, ensure_ascii=False, sort_keys=True)
    new_sort = row["sort_order"] if sort_order is None else int(sort_order)
    cleaned_reason = clean_text(reason, "reason", max_len=NOTE_MAX, required=False) or None

    conn.execute(
        "UPDATE public_contents SET title = ?, summary = ?, body_html = ?, meta_json = ?, sort_order = ?, "
        "version = version + 1, updated_at = ?, updated_by = ? WHERE id = ?",
        (new_title, new_summary, new_html, new_meta, new_sort, to_db(now), actor, row["id"]),
    )
    updated = _content_row_by_code(conn, row["code"])
    _record_revision(conn, updated, actor=actor, now=now, reason=cleaned_reason)
    write_audit(
        conn,
        actor=actor,
        action="CMS_CONTENT_UPDATE",
        object_type="PublicContent",
        object_code=row["code"],
        at=now,
        before={"title": row["title"], "version": row["version"]},
        after={"title": new_title, "version": updated["version"]},
        reason=cleaned_reason,
    )
    return get_cms_content(conn, row["code"])


def set_cms_content_status(
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
    if status not in CMS_STATUSES:
        raise ValidationFailed("Trạng thái CMS không hợp lệ.", details={"field": "status"})
    row = _content_row_by_code(conn, code)
    check_version(row["version"], expected_version)
    cleaned_reason = clean_text(reason, "reason", max_len=NOTE_MAX, required=False) or None

    # PUB-05: Không cho phép ẩn cả 3 trang dịch vụ.
    if row["content_type"] == "SERVICE" and row["status"] == "PUBLISHED" and status != "PUBLISHED":
        remaining_services = conn.execute(
            "SELECT COUNT(DISTINCT service_type) FROM public_contents "
            "WHERE content_type = 'SERVICE' AND status = 'PUBLISHED' AND id <> ?",
            (row["id"],),
        ).fetchone()[0]
        if remaining_services == 0:
            raise Conflict(
                "Không thể ẩn hoặc gỡ xuất bản cả 3 trang dịch vụ công khai. "
                "Phải có ít nhất một trang dịch vụ ở trạng thái PUBLISHED.",
                code="ALL_SERVICES_HIDDEN",
                details={"code": row["code"], "service_type": row["service_type"]},
            )

    now_s = to_db(now)
    published_at = now_s if status == "PUBLISHED" else row["published_at"]
    published_by = actor if status == "PUBLISHED" else row["published_by"]

    conn.execute(
        "UPDATE public_contents SET status = ?, published_at = ?, published_by = ?, "
        "version = version + 1, updated_at = ?, updated_by = ? WHERE id = ?",
        (status, published_at, published_by, now_s, actor, row["id"]),
    )
    updated = _content_row_by_code(conn, row["code"])
    _record_revision(conn, updated, actor=actor, now=now, reason=cleaned_reason)
    write_audit(
        conn,
        actor=actor,
        action="CMS_CONTENT_STATUS",
        object_type="PublicContent",
        object_code=row["code"],
        at=now,
        before={"status": row["status"]},
        after={"status": status},
        reason=cleaned_reason,
    )
    return get_cms_content(conn, row["code"])


def get_cms_content(conn: sqlite3.Connection, code: str) -> dict:
    row = _content_row_by_code(conn, code)
    data = _content_to_dict(row, public_view=False)
    revisions = conn.execute(
        "SELECT version_no, title, summary, status, changed_at, changed_by, reason "
        "FROM public_content_revisions WHERE content_code = ? ORDER BY version_no DESC",
        (row["code"],),
    ).fetchall()
    data["revisions"] = [dict(r) for r in revisions]
    return data


def list_cms_contents(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    content_type: str | None = None,
    service_type: str | None = None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    where: list[str] = []
    params: list[Any] = []
    if status:
        where.append("status = ?")
        params.append(status)
    if content_type:
        where.append("content_type = ?")
        params.append(content_type)
    if service_type:
        where.append("service_type = ?")
        params.append(service_type)
    if q and q.strip():
        needle = f"%{q.strip()}%"
        where.append("(code LIKE ? OR slug LIKE ? OR title LIKE ?)")
        params.extend([needle.upper(), needle.lower(), needle])
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    total = conn.execute("SELECT COUNT(*) FROM public_contents" + clause, params).fetchone()[0]
    rows = conn.execute(
        f"SELECT * FROM public_contents{clause} ORDER BY sort_order ASC, id DESC LIMIT ? OFFSET ?",
        (*params, limit, offset),
    ).fetchall()
    return {
        "items": [_content_to_dict(r, public_view=False) for r in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


# ---------- PUB-02 & PUB-03: Gửi yêu cầu dịch vụ từ website & cấp token >= 128 bit ----------

def _check_catalog_active_if_configured(
    conn: sqlite3.Connection, service_type: str, service_data: dict
) -> None:
    if service_type == "RENTAL" and isinstance(service_data, dict):
        vtype = str(service_data.get("vehicle_type") or "").strip().upper()
        if vtype and conn.execute("SELECT EXISTS(SELECT 1 FROM catalog_vehicle_types)").fetchone()[0]:
            row = conn.execute(
                "SELECT status FROM catalog_vehicle_types WHERE code = ?", (vtype,)
            ).fetchone()
            if row is None or row["status"] != "ACTIVE":
                raise ValidationFailed(
                    "Loại xe được chọn không tồn tại hoặc đã ngừng cung cấp.",
                    details={"field": "vehicle_type"},
                )
    elif service_type == "WAREHOUSE" and isinstance(service_data, dict):
        lines = service_data.get("lines")
        if isinstance(lines, list) and conn.execute("SELECT EXISTS(SELECT 1 FROM catalog_units)").fetchone()[0]:
            for idx, line in enumerate(lines):
                if isinstance(line, dict):
                    ucode = str(line.get("unit_code") or "").strip().upper()
                    if ucode:
                        row = conn.execute(
                            "SELECT status FROM catalog_units WHERE code = ?", (ucode,)
                        ).fetchone()
                        if row is None or row["status"] != "ACTIVE":
                            raise ValidationFailed(
                                f"Đơn vị hàng hóa '{ucode}' không tồn tại hoặc đã ngừng sử dụng.",
                                details={"field": f"lines.{idx}.unit_code"},
                            )


def _count_rate_events(conn: sqlite3.Connection, bucket: str, subject_key: str, since: datetime) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM rate_limit_events WHERE bucket = ? AND subject_key = ? AND occurred_at > ?",
        (bucket, subject_key, to_db(since)),
    ).fetchone()
    return row[0] if row else 0


def _record_rate_event(conn: sqlite3.Connection, bucket: str, subject_key: str, now: datetime) -> None:
    conn.execute(
        "INSERT INTO rate_limit_events(bucket, subject_key, occurred_at) VALUES (?, ?, ?)",
        (bucket, subject_key, to_db(now)),
    )


def submit_public_request(
    conn: sqlite3.Connection,
    *,
    client_ip: str,
    service_type: str,
    contact: dict,
    service_data: dict,
    now: datetime,
) -> dict:
    """PUB-02 & PUB-03: Nhận yêu cầu dịch vụ từ website công khai.

    - Giới hạn tối đa 5 lần gửi / IP / 10 phút.
    - Chống XSS / script injection trên toàn bộ trường contact và service_data.
    - Sinh token tra cứu >= 128 bit (192-bit `secrets.token_urlsafe(24)`), lưu SHA-256 trong
      `service_requests.public_token_hash`, chỉ trả token dạng rõ 1 lần duy nhất.
    """
    require_tx(conn)
    ip_key = (client_ip or "unknown").strip() or "unknown"
    now_u = to_utc(now)
    recent_submits = _count_rate_events(conn, "PUB_SUBMIT", ip_key, now_u - SUBMIT_RATE_WINDOW)
    if recent_submits >= SUBMIT_RATE_LIMIT:
        raise RateLimited(
            "Bạn đã gửi quá 5 yêu cầu dịch vụ trong vòng 10 phút. Vui lòng thử lại sau.",
            details={"limit": SUBMIT_RATE_LIMIT, "window_minutes": 10},
        )

    assert_no_script_payload(contact, "contact")
    assert_no_script_payload(service_data, "service_data")
    _check_catalog_active_if_configured(conn, service_type, service_data)

    # Token >= 128 bit (24 bytes = 192 bits).
    token = secrets.token_urlsafe(24)
    created = sales.create_request(
        conn,
        service_type=service_type,
        source="WEBSITE",
        contact=contact,
        service_data=service_data,
        actor="public",
        now=now_u,
        token=token,
    )
    _record_rate_event(conn, "PUB_SUBMIT", ip_key, now_u)

    return {
        "code": created["code"],
        "request_code": created["code"],
        "token": token,
        "lookup_token": token,
        "service_type": created["service_type"],
        "source": "WEBSITE",
        "status": created["status"],
        "created_at": created["created_at"],
        "notice": "Lưu lại mã yêu cầu và token tra cứu. Token chỉ hiển thị một lần duy nhất.",
    }


# ---------- PUB-04: Tra cứu tiến độ công khai theo BR-18 ----------

def _public_milestones(conn: sqlite3.Connection, service_type: str, operational_code: str) -> tuple[str | None, list[dict]]:
    if service_type == "TRANSPORT":
        trip = conn.execute(
            "SELECT code, status FROM transport_trips WHERE code = ?", (operational_code,)
        ).fetchone()
        if trip is None:
            return None, []
        rows = conn.execute(
            "SELECT milestone, actual_at FROM trip_milestones WHERE trip_code = ? ORDER BY id",
            (trip["code"],),
        ).fetchall()
        return trip["status"], [{"milestone": r["milestone"], "occurred_at": r["actual_at"]} for r in rows]

    if service_type == "RENTAL":
        rental = conn.execute(
            "SELECT code, status FROM rental_records WHERE code = ?", (operational_code,)
        ).fetchone()
        if rental is None:
            return None, []
        rows = conn.execute(
            "SELECT kind, actual_at FROM rental_inspections WHERE rental_code = ? ORDER BY id",
            (rental["code"],),
        ).fetchall()
        return rental["status"], [{"milestone": r["kind"], "occurred_at": r["actual_at"]} for r in rows]

    if service_type == "WAREHOUSE":
        lot = conn.execute(
            "SELECT id, code, status FROM warehouse_lots WHERE code = ?", (operational_code,)
        ).fetchone()
        if lot is None:
            return None, []
        rows = conn.execute(
            "SELECT doc_type, COALESCE(occurred_at, posted_at) AS at_time "
            "FROM warehouse_documents WHERE lot_id = ? AND status = 'POSTED' "
            "AND doc_type IN ('RECEIPT', 'ISSUE') ORDER BY id",
            (lot["id"],),
        ).fetchall()
        return lot["status"], [{"milestone": r["doc_type"], "occurred_at": r["at_time"]} for r in rows]

    return None, []


def lookup_public_progress(
    conn: sqlite3.Connection,
    *,
    client_ip: str,
    request_code: str,
    token: str,
    now: datetime,
) -> dict:
    """PUB-04: Tra cứu tiến độ công khai bằng mã yêu cầu + token qua POST.

    - Chặn sau 10 lần sai / IP / 15 phút (429 RATE_LIMITED).
    - So sánh băm SHA-256 của token bằng `hmac.compare_digest`.
    - Chỉ trả các trường BR-18 cho phép; tuyệt đối không lộ ghi chú nội bộ, công nợ,
      nhật ký sự kiện nội bộ hay `public_token_hash`.
    """
    ip_key = (client_ip or "unknown").strip() or "unknown"
    now_u = to_utc(now)

    recent_fails = _count_rate_events(conn, "PUB_LOOKUP_FAIL", ip_key, now_u - LOOKUP_FAIL_WINDOW)
    if recent_fails >= LOOKUP_FAIL_LIMIT:
        raise RateLimited(
            "Tạm khóa tra cứu do nhập sai mã hoặc token quá 10 lần trong 15 phút. Vui lòng thử lại sau.",
            details={"limit": LOOKUP_FAIL_LIMIT, "window_minutes": 15},
        )

    cleaned_code = clean_text(request_code, "request_code", max_len=50).upper()
    cleaned_token = clean_text(token, "token", max_len=200)
    candidate_hash = hashlib.sha256(cleaned_token.encode("utf-8")).hexdigest()

    row = conn.execute(
        "SELECT code, service_type, status, public_token_hash, created_at, updated_at "
        "FROM service_requests WHERE code = ?",
        (cleaned_code,),
    ).fetchone()

    valid = (
        row is not None
        and row["public_token_hash"] is not None
        and hmac.compare_digest(row["public_token_hash"], candidate_hash)
    )
    if not valid:
        with write_tx(conn):
            _record_rate_event(conn, "PUB_LOOKUP_FAIL", ip_key, now_u)
        raise Unauthorized(
            "Mã yêu cầu hoặc token tra cứu không đúng.",
            code="INVALID_LOOKUP_CREDENTIALS",
        )

    order = conn.execute(
        "SELECT code, status, operational_code, created_at, updated_at "
        "FROM service_orders WHERE request_code = ?",
        (row["code"],),
    ).fetchone()

    order_code = None
    order_status = None
    operational_status = None
    milestones: list[dict] = []
    if order is not None:
        order_code = order["code"]
        order_status = order["status"]
        operational_status, milestones = _public_milestones(
            conn, row["service_type"], order["operational_code"]
        )

    return {
        "request_code": row["code"],
        "service_type": row["service_type"],
        "request_status": row["status"],
        "submitted_at": row["created_at"],
        "updated_at": row["updated_at"],
        "order_code": order_code,
        "order_status": order_status,
        "operational_status": operational_status,
        "milestones": milestones,
        "support_contact": PUBLIC_SUPPORT_CONTACT,
    }
