"""Nghiệp vụ khách hàng, báo giá, đơn và công nợ (BE1)."""

import json
import hashlib
import math
import secrets
from datetime import date, datetime, time

from . import assignments, attachments, warehouse
from .audit import write_audit
from .db import next_code, require_tx
from .errors import Conflict, NotFound, ValidationFailed
from .integrations import hooks
from .timeutil import SYSTEM_TZ, to_db, to_utc
from .validation import check_version, clean_text


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _row(conn, table, code):
    row = conn.execute(f"SELECT * FROM {table} WHERE code=?", (code,)).fetchone()
    if row is None:
        raise NotFound("Không tìm thấy hồ sơ.", details={"code": code})
    return dict(row)


def _phone(value):
    value = clean_text(value, "phone", max_len=30)
    digits = "".join(c for c in value if c.isdigit())
    if (
        len(digits) < 8
        or len(digits) > 15
        or any(c not in "+0123456789 ()-" for c in value)
    ):
        raise ValidationFailed(
            "Số điện thoại cần có 8–15 chữ số.", details={"field": "phone"}
        )
    prefix = "+" if value.lstrip().startswith("+") else ""
    return prefix + digits


def _email(value):
    if value is not None and (
        "@" not in value or value.startswith("@") or value.endswith("@")
    ):
        raise ValidationFailed(
            "Email không đúng định dạng.", details={"field": "email"}
        )
    return value


def customer(conn, code):
    return _row(conn, "customers", code)


def customer_detail(conn, code):
    profile = customer(conn, code)
    profile["requests"] = [
        dict(row)
        for row in conn.execute(
            "SELECT code,service_type,source,status,assigned_to,created_at "
            "FROM service_requests WHERE customer_code=? ORDER BY created_at DESC",
            (code,),
        )
    ]
    profile["orders"] = [
        {**dict(row), "balance": order_balance(conn, row["code"])}
        for row in conn.execute(
            "SELECT code,service_type,status,price_snapshot_vnd,payment_due_date,created_at "
            "FROM service_orders WHERE customer_code=? ORDER BY created_at DESC",
            (code,),
        )
    ]
    return profile


def list_customers(conn, *, q=None, status=None, limit=50, offset=0):
    where = []
    args = []
    if status:
        where.append("status=?")
        args.append(status)
    if q:
        where.append("(code LIKE ? OR name LIKE ? OR phone LIKE ?)")
        args += [f"%{q}%"] * 3
    sql = (
        "SELECT * FROM customers"
        + (" WHERE " + " AND ".join(where) if where else "")
        + " ORDER BY name LIMIT ? OFFSET ?"
    )
    args += [limit, offset]
    return {
        "items": [dict(r) for r in conn.execute(sql, args)],
        "limit": limit,
        "offset": offset,
    }


def create_customer(
    conn,
    *,
    customer_type,
    name,
    contact_name,
    phone,
    email=None,
    address=None,
    duplicate_reason=None,
    actor,
    now,
):
    require_tx(conn)
    name = clean_text(name, "name", max_len=200)
    contact_name = clean_text(contact_name, "contact_name", max_len=200)
    phone = _phone(phone)
    email = _email(email)
    dup = conn.execute(
        "SELECT code,name,phone,email FROM customers WHERE status='ACTIVE' AND (phone=? OR (? IS NOT NULL AND lower(email)=lower(?)))",
        (phone, email, email),
    ).fetchall()
    if dup and not duplicate_reason:
        raise Conflict(
            "Có hồ sơ khách hàng trùng thông tin. Hãy chọn hồ sơ hoặc nhập lý do tạo riêng.",
            code="CUSTOMER_DUPLICATE",
            details={"matches": [dict(x) for x in dup]},
        )
    code = next_code(conn, "CUS")
    ts = to_db(now)
    conn.execute(
        "INSERT INTO customers(code,customer_type,name,contact_name,phone,email,address,status,created_at,created_by,updated_at,updated_by) VALUES(?,?,?,?,?,?,?,'ACTIVE',?,?,?,?)",
        (
            code,
            customer_type,
            name,
            contact_name,
            phone,
            email,
            address,
            ts,
            actor,
            ts,
            actor,
        ),
    )
    write_audit(
        conn,
        actor=actor,
        action="CUSTOMER_CREATE",
        object_type="Customer",
        object_code=code,
        at=now,
        after={"name": name, "status": "ACTIVE"},
        reason=duplicate_reason,
    )
    return customer(conn, code)


def update_customer(conn, code, *, version, actor, now, **fields):
    require_tx(conn)
    row = customer(conn, code)
    check_version(row["version"], version)
    duplicate_reason = fields.pop("duplicate_reason", None)
    allowed = {"name", "contact_name", "phone", "email", "address"}
    fields = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not fields:
        return row
    for k, v in fields.items():
        if k in ("name", "contact_name"):
            fields[k] = clean_text(v, k, max_len=200)
        if k == "phone":
            fields[k] = _phone(v)
        if k == "email":
            fields[k] = _email(v)
    next_phone = fields.get("phone", row["phone"])
    next_email = fields.get("email", row["email"])
    duplicates = conn.execute(
        "SELECT code,name,phone,email FROM customers "
        "WHERE status='ACTIVE' AND code<>? AND "
        "(phone=? OR (? IS NOT NULL AND lower(email)=lower(?)))",
        (code, next_phone, next_email, next_email),
    ).fetchall()
    if duplicates and not duplicate_reason:
        raise Conflict(
            "Có hồ sơ khách hàng trùng thông tin. Nhập lý do xác nhận cập nhật.",
            code="CUSTOMER_DUPLICATE",
            details={"matches": [dict(item) for item in duplicates]},
        )
    sets = ", ".join(f"{k}=?" for k in fields)
    vals = list(fields.values()) + [to_db(now), actor, code, version]
    cur = conn.execute(
        f"UPDATE customers SET {sets},version=version+1,updated_at=?,updated_by=? WHERE code=? AND version=?",
        vals,
    )
    if not cur.rowcount:
        raise Conflict("Hồ sơ đã thay đổi.", code="VERSION_CONFLICT")
    write_audit(
        conn,
        actor=actor,
        action="CUSTOMER_UPDATE",
        object_type="Customer",
        object_code=code,
        at=now,
        before={k: row[k] for k in fields},
        after=fields,
        reason=duplicate_reason,
    )
    return customer(conn, code)


def set_customer_status(conn, code, *, version, status, actor, now):
    require_tx(conn)
    row = customer(conn, code)
    check_version(row["version"], version)
    if status not in ("ACTIVE", "INACTIVE"):
        raise ValidationFailed("Trạng thái khách hàng không hợp lệ.")
    conn.execute(
        "UPDATE customers SET status=?,version=version+1,updated_at=?,updated_by=? WHERE code=?",
        (status, to_db(now), actor, code),
    )
    write_audit(
        conn,
        actor=actor,
        action="CUSTOMER_STATUS",
        object_type="Customer",
        object_code=code,
        at=now,
        before={"status": row["status"]},
        after={"status": status},
    )
    return customer(conn, code)


def create_request(
    conn, *, service_type, source, contact, service_data, actor, now, token=None
):
    require_tx(conn)
    if service_type not in ("TRANSPORT", "RENTAL", "WAREHOUSE"):
        raise ValidationFailed(
            "Loại dịch vụ không hợp lệ.", details={"field": "service_type"}
        )
    if source not in ("WEBSITE", "INTERNAL"):
        raise ValidationFailed(
            "Nguồn yêu cầu không hợp lệ.", details={"field": "source"}
        )
    _validate_service_data(service_type, service_data)
    if not isinstance(contact, dict):
        raise ValidationFailed(
            "Thông tin liên hệ không hợp lệ.", details={"field": "contact"}
        )
    for field in ("name", "contact_name", "phone"):
        if not contact.get(field):
            raise ValidationFailed(f"Thiếu {field}.", details={"field": field})
    contact = {
        **contact,
        "name": clean_text(contact["name"], "name", max_len=200),
        "contact_name": clean_text(
            contact["contact_name"], "contact_name", max_len=200
        ),
        "phone": _phone(contact["phone"]),
        "email": _email(
            clean_text(contact["email"], "email", max_len=254, required=False)
            if contact.get("email")
            else None
        ),
    }
    code = next_code(conn, "REQ")
    ts = to_db(now)
    token_hash = hashlib.sha256(token.encode()).hexdigest() if token else None
    conn.execute(
        "INSERT INTO service_requests(code,service_type,source,initial_contact_json,service_data_json,status,public_token_hash,created_at,created_by,updated_at,updated_by) VALUES(?,?,?,?,?,'NEW',?,?,?, ?,?)",
        (
            code,
            service_type,
            source,
            _json(contact),
            _json(service_data),
            token_hash,
            ts,
            actor,
            ts,
            actor,
        ),
    )
    write_audit(
        conn,
        actor=actor,
        action="REQUEST_CREATE",
        object_type="ServiceRequest",
        object_code=code,
        at=now,
        after={"service_type": service_type, "source": source, "status": "NEW"},
    )
    return {
        **_row(conn, "service_requests", code),
        "initial_contact": contact,
        "service_data": service_data,
        "token": token,
    }


def _append_request_event(
    conn,
    request_code,
    *,
    event_type,
    content,
    actor,
    actual_at,
    recorded_at,
):
    conn.execute(
        "INSERT INTO request_events "
        "(request_code,event_type,content,actual_at,recorded_at,recorded_by) "
        "VALUES(?,?,?,?,?,?)",
        (
            request_code,
            event_type,
            _json(content) if not isinstance(content, str) else content,
            to_db(actual_at),
            to_db(recorded_at),
            actor,
        ),
    )


def _validate_service_data(service_type, service_data):
    """Validate the required fields for each of the three request types."""
    if not isinstance(service_data, dict):
        raise ValidationFailed(
            "Thông tin dịch vụ không hợp lệ.", details={"field": "service_data"}
        )
    required = {
        "TRANSPORT": (
            "pickup_address",
            "delivery_address",
            "cargo_description",
            "cargo_weight_kg",
            "planned_pickup_at",
        ),
        "RENTAL": (
            "vehicle_type",
            "min_capacity_kg",
            "requested_start",
            "requested_end",
            "purpose",
        ),
        "WAREHOUSE": (
            "cargo_description",
            "expected_in_at",
            "expected_out_at",
            "lines",
        ),
    }.get(service_type)
    if required is None:
        raise ValidationFailed(
            "Loại dịch vụ không hợp lệ.", details={"field": "service_type"}
        )
    for field in required:
        value = service_data.get(field)
        if value is None or value == "":
            raise ValidationFailed(
                "Thiếu trường bắt buộc của dịch vụ.", details={"field": field}
            )
    if service_type == "TRANSPORT":
        try:
            weight = float(service_data["cargo_weight_kg"])
        except (TypeError, ValueError):
            weight = 0
        if not math.isfinite(weight) or weight <= 0:
            raise ValidationFailed(
                "Khối lượng phải là số dương hợp lệ.",
                details={"field": "cargo_weight_kg"},
            )
        for field in ("pickup_address", "delivery_address", "cargo_description"):
            clean_text(service_data[field], field, max_len=1000)
        planned_pickup = _parse_service_datetime(
            service_data["planned_pickup_at"], "planned_pickup_at"
        )
        if service_data.get("committed_end"):
            committed_end = _parse_service_datetime(
                service_data["committed_end"], "committed_end"
            )
            if to_utc(committed_end) <= to_utc(planned_pickup):
                raise ValidationFailed(
                    "Thời điểm kết thúc cam kết phải sau giờ lấy hàng.",
                    details={"field": "committed_end"},
                )
        if "pickup_from_warehouse" in service_data and not isinstance(
            service_data["pickup_from_warehouse"], bool
        ):
            raise ValidationFailed(
                "Cờ lấy hàng từ kho cần là true hoặc false.",
                details={"field": "pickup_from_warehouse"},
            )
    if service_type == "RENTAL":
        try:
            capacity = int(service_data["min_capacity_kg"])
        except (TypeError, ValueError):
            capacity = 0
        if capacity <= 0:
            raise ValidationFailed(
                "Tải trọng phải lớn hơn 0.", details={"field": "min_capacity_kg"}
            )
        clean_text(service_data["vehicle_type"], "vehicle_type", max_len=100)
        clean_text(service_data["purpose"], "purpose", max_len=1000)
        start = _parse_service_datetime(
            service_data["requested_start"], "requested_start"
        )
        end = _parse_service_datetime(service_data["requested_end"], "requested_end")
        if to_utc(end) <= to_utc(start):
            raise ValidationFailed(
                "Thời điểm kết thúc phải sau thời điểm bắt đầu.",
                details={"field": "requested_end"},
            )
    if service_type == "WAREHOUSE":
        clean_text(service_data["cargo_description"], "cargo_description", max_len=1000)
        expected_in = _parse_service_datetime(
            service_data["expected_in_at"], "expected_in_at"
        )
        expected_out = _parse_service_datetime(
            service_data["expected_out_at"], "expected_out_at"
        )
        if to_utc(expected_out) <= to_utc(expected_in):
            raise ValidationFailed(
                "Thời điểm xuất dự kiến phải sau thời điểm nhập.",
                details={"field": "expected_out_at"},
            )
        if not isinstance(service_data["lines"], list) or not service_data["lines"]:
            raise ValidationFailed(
                "Kho cần ít nhất một dòng hàng.", details={"field": "lines"}
            )
        for index, line in enumerate(service_data["lines"]):
            if not isinstance(line, dict):
                raise ValidationFailed(
                    "Dòng hàng không hợp lệ.", details={"field": f"lines.{index}"}
                )
            try:
                quantity = int(line.get("expected_qty", 0))
            except (TypeError, ValueError):
                quantity = 0
            if quantity <= 0:
                raise ValidationFailed(
                    "Số lượng phải là số nguyên dương.",
                    details={"field": f"lines.{index}.expected_qty"},
                )
            for field in ("item_code", "item_name", "unit_code"):
                clean_text(line.get(field), f"lines.{index}.{field}", max_len=200)


def _parse_service_datetime(value, field):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValidationFailed(
            "Thời điểm dịch vụ không đúng định dạng ISO.", details={"field": field}
        ) from exc


def get_request(conn, code):
    r = _row(conn, "service_requests", code)
    r["initial_contact"] = json.loads(r.pop("initial_contact_json"))
    r["service_data"] = json.loads(r.pop("service_data_json"))
    r["events"] = []
    for event in conn.execute(
        "SELECT id,event_type,content,actual_at,recorded_at,recorded_by "
        "FROM request_events WHERE request_code=? ORDER BY actual_at,id",
        (code,),
    ):
        try:
            content = json.loads(event["content"])
        except (TypeError, json.JSONDecodeError):
            content = event["content"]
        r["events"].append({**dict(event), "content": content})
    return r


def list_requests(
    conn,
    *,
    status=None,
    service_type=None,
    assigned_to=None,
    unassigned_only=False,
    q=None,
    limit=50,
    offset=0,
):
    w = []
    a = []
    for col, val in (("status", status), ("service_type", service_type)):
        if val:
            w.append(f"{col}=?")
            a.append(val)
    if assigned_to:
        w.append("assigned_to=?")
        a.append(assigned_to)
    if unassigned_only:
        w.append("assigned_to IS NULL")
    if q:
        w.append("(code LIKE ? OR assigned_to LIKE ? OR initial_contact_json LIKE ?)")
        a += [f"%{q}%"] * 3
    where = " WHERE " + " AND ".join(w) if w else ""
    total = conn.execute("SELECT COUNT(*) FROM service_requests" + where, a).fetchone()[
        0
    ]
    rows = conn.execute(
        "SELECT code,service_type,source,customer_code,initial_contact_json,status,"
        "assigned_to,created_at,version FROM service_requests"
        + where
        + " ORDER BY created_at DESC LIMIT ? OFFSET ?",
        [*a, limit, offset],
    )
    items = []
    for row in rows:
        item = dict(row)
        item["initial_contact"] = json.loads(item.pop("initial_contact_json"))
        items.append(item)
    return {"items": items, "limit": limit, "offset": offset, "total": total}


def update_request(conn, code, *, version, actor, now, **fields):
    require_tx(conn)
    r = get_request(conn, code)
    check_version(r["version"], version)
    if not fields:
        return r
    new_status = fields.get("status", r["status"])
    allowed = {
        "NEW": {"CLARIFYING", "CLOSED_LOST"},
        "CLARIFYING": {"QUOTING", "CLOSED_LOST"},
        "QUOTING": {"CLARIFYING", "CLOSED_LOST"},
    }
    if new_status != r["status"] and new_status not in allowed.get(r["status"], set()):
        raise Conflict("Chuyển trạng thái yêu cầu không hợp lệ.", code="INVALID_STATE")
    customer_code = fields.get("customer_code", r["customer_code"])
    if customer_code:
        c = customer(conn, customer_code)
        if c["status"] != "ACTIVE":
            raise Conflict(
                "Không thể gắn khách hàng ngừng hoạt động.", code="CUSTOMER_INACTIVE"
            )
    if new_status == "CLOSED_LOST" and not fields.get("reason"):
        raise ValidationFailed("Cần lý do đóng yêu cầu.", details={"field": "reason"})
    assigned_to = fields.get("assigned_to") or r["assigned_to"]
    if assigned_to is not None:
        assigned_to = clean_text(assigned_to, "assigned_to", max_len=100)
    conn.execute(
        "UPDATE service_requests SET status=?,customer_code=?,assigned_to=COALESCE(?,assigned_to),closed_reason=?,version=version+1,updated_at=?,updated_by=? WHERE code=?",
        (
            new_status,
            customer_code,
            assigned_to if assigned_to != r["assigned_to"] else None,
            fields.get("reason"),
            to_db(now),
            actor,
            code,
        ),
    )
    if new_status != r["status"]:
        _append_request_event(
            conn,
            code,
            event_type="STATUS_CHANGED",
            content={"from": r["status"], "to": new_status},
            actor=actor,
            actual_at=now,
            recorded_at=now,
        )
    if assigned_to != r["assigned_to"]:
        _append_request_event(
            conn,
            code,
            event_type="ASSIGNED",
            content={"from": r["assigned_to"], "to": assigned_to},
            actor=actor,
            actual_at=now,
            recorded_at=now,
        )
    if fields.get("clarification"):
        conn.execute(
            "INSERT INTO request_events(request_code,event_type,content,actual_at,recorded_at,recorded_by) VALUES(?,?,?,?,?,?)",
            (
                code,
                "CLARIFICATION",
                clean_text(fields["clarification"], "clarification", max_len=2000),
                to_db(fields.get("actual_at", now)),
                to_db(now),
                actor,
            ),
        )
    write_audit(
        conn,
        actor=actor,
        action="REQUEST_UPDATE",
        object_type="ServiceRequest",
        object_code=code,
        at=now,
        before={
            "status": r["status"],
            "customer_code": r["customer_code"],
            "assigned_to": r["assigned_to"],
        },
        after={
            "status": new_status,
            "customer_code": customer_code,
            "assigned_to": assigned_to,
        },
        reason=fields.get("reason"),
    )
    return get_request(conn, code)


def record_contact(
    conn, request_code, *, channel, content, follow_up, actual_at, actor, now
):
    """Append a contact attempt; historical interaction records are immutable."""
    require_tx(conn)
    request = get_request(conn, request_code)
    if request["status"] in ("CONFIRMED", "CLOSED_LOST"):
        raise Conflict(
            "Không thể ghi trao đổi mới cho yêu cầu đã đóng.", code="INVALID_STATE"
        )
    channel = clean_text(channel, "channel", max_len=30)
    content = clean_text(content, "content", max_len=2000)
    if channel not in ("PHONE", "EMAIL", "IN_PERSON"):
        raise ValidationFailed(
            "Kênh liên hệ không hợp lệ.", details={"field": "channel"}
        )
    actual_time = to_utc(actual_at)
    if actual_time > to_utc(now):
        raise ValidationFailed(
            "Thời điểm trao đổi không được ở tương lai.", details={"field": "actual_at"}
        )

    conn.execute(
        "INSERT INTO request_events(request_code,event_type,content,actual_at,recorded_at,recorded_by) "
        "VALUES(?, 'CONTACT:' || ?, ?, ?, ?, ?)",
        (
            request_code,
            channel,
            _json({"content": content, "follow_up": follow_up}),
            to_db(actual_time),
            to_db(now),
            actor,
        ),
    )
    write_audit(
        conn,
        actor=actor,
        action="REQUEST_CONTACT",
        object_type="ServiceRequest",
        object_code=request_code,
        at=now,
        after={"channel": channel, "actual_at": to_db(actual_time)},
    )
    return get_request(conn, request_code)


def correct_request_event(
    conn, request_code, event_id, *, corrected_content, reason, actor, now
):
    """Append a correction that points to history; never rewrite old events."""
    require_tx(conn)
    get_request(conn, request_code)
    original = conn.execute(
        "SELECT id,event_type,content FROM request_events "
        "WHERE id=? AND request_code=?",
        (event_id, request_code),
    ).fetchone()
    if original is None:
        raise NotFound("Không tìm thấy nhật ký trao đổi cần đính chính.")
    corrected_content = clean_text(corrected_content, "corrected_content", max_len=2000)
    reason = clean_text(reason, "reason", max_len=2000)
    conn.execute(
        "INSERT INTO request_events(request_code,event_type,content,actual_at,recorded_at,recorded_by) "
        "VALUES(?, 'CORRECTION', ?, ?, ?, ?)",
        (
            request_code,
            _json(
                {
                    "corrects_event_id": event_id,
                    "original_event_type": original["event_type"],
                    "corrected_content": corrected_content,
                    "reason": reason,
                }
            ),
            to_db(now),
            to_db(now),
            actor,
        ),
    )
    write_audit(
        conn,
        actor=actor,
        action="REQUEST_EVENT_CORRECT",
        object_type="ServiceRequest",
        object_code=request_code,
        at=now,
        before={"event_id": event_id, "event_type": original["event_type"]},
        after={"corrected_content": corrected_content},
        reason=reason,
    )
    return {
        "request_code": request_code,
        "corrects_event_id": event_id,
        "corrected_content": corrected_content,
        "reason": reason,
    }


def rotate_request_token(conn, request_code, *, verification, reason, actor, now):
    """Replace the public lookup token and return the new plaintext once."""
    require_tx(conn)
    request = get_request(conn, request_code)
    verification = clean_text(verification, "verification", max_len=2000)
    reason = clean_text(reason, "reason", max_len=2000)
    token = secrets.token_urlsafe(24)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    conn.execute(
        "UPDATE service_requests SET public_token_hash=?, version=version+1, "
        "updated_at=?, updated_by=? WHERE code=?",
        (token_hash, to_db(now), actor, request_code),
    )
    conn.execute(
        "INSERT INTO request_events(request_code,event_type,content,actual_at,recorded_at,recorded_by) "
        "VALUES(?, 'TOKEN_ROTATED', ?, ?, ?, ?)",
        (
            request_code,
            _json({"verification": verification, "reason": reason}),
            to_db(now),
            to_db(now),
            actor,
        ),
    )
    write_audit(
        conn,
        actor=actor,
        action="REQUEST_TOKEN_ROTATE",
        object_type="ServiceRequest",
        object_code=request_code,
        at=now,
        after={"token_rotated": True},
        reason=reason,
    )
    return {"request_code": request_code, "token": token}


def quote_detail(conn, code, *, now):
    quote = _quote(conn, code)
    if quote["status"] == "SENT":
        expires_at = datetime.combine(
            date.fromisoformat(quote["valid_until"]), time.max, tzinfo=SYSTEM_TZ
        )
        if now.astimezone(SYSTEM_TZ) > expires_at:
            quote["status"] = "EXPIRED"
    if quote["response_json"]:
        quote["response"] = json.loads(quote["response_json"])
    else:
        quote["response"] = None
    quote.pop("response_json", None)
    return quote


def _quote(conn, code):
    r = _row(conn, "quotes", code)
    r["lines"] = [
        dict(x)
        for x in conn.execute(
            "SELECT description,quantity,unit_price_vnd,line_total_vnd FROM quote_lines WHERE quote_code=? ORDER BY id",
            (code,),
        )
    ]
    r["service_data"] = json.loads(r.pop("service_data_json"))
    return r


def create_quote(
    conn, *, request_code, valid_until, service_data, terms="", lines, actor, now
):
    require_tx(conn)
    req = get_request(conn, request_code)
    if (
        req["status"] not in ("NEW", "CLARIFYING", "QUOTING")
        or not req["customer_code"]
    ):
        raise Conflict(
            "Yêu cầu cần khách hàng ACTIVE và đủ thông tin trước khi lập báo giá.",
            code="REQUEST_NOT_READY",
        )
    if customer(conn, req["customer_code"])["status"] != "ACTIVE":
        raise Conflict("Khách hàng đã ngừng hoạt động.", code="CUSTOMER_INACTIVE")
    _validate_service_data(req["service_type"], service_data)
    if not lines:
        raise ValidationFailed(
            "Báo giá cần ít nhất một dòng.", details={"field": "lines"}
        )
    expiry = date.fromisoformat(str(valid_until)[:10])
    if expiry < now.astimezone(SYSTEM_TZ).date():
        raise ValidationFailed(
            "Ngày hiệu lực không được ở quá khứ.", details={"field": "valid_until"}
        )
    total = 0
    normalized = []
    for line in lines:
        qty = int(line["quantity"])
        price = int(line["unit_price_vnd"])
        if qty <= 0 or price < 0:
            raise ValidationFailed("Số lượng phải dương, đơn giá không âm.")
        amount = qty * price
        total += amount
        normalized.append(
            (
                clean_text(line["description"], "description", max_len=200),
                qty,
                price,
                amount,
            )
        )
    if total <= 0:
        raise ValidationFailed("Báo giá phải có ít nhất một dòng có giá trị dương.")
    prev = conn.execute(
        "SELECT * FROM quotes WHERE request_code=? ORDER BY version_no DESC LIMIT 1",
        (request_code,),
    ).fetchone()
    version = (prev["version_no"] + 1) if prev else 1
    code = next_code(conn, "QUO")
    ts = to_db(now)
    conn.execute(
        "INSERT INTO quotes(code,request_code,version_no,previous_quote_code,valid_until,service_data_json,terms,total_vnd,status,created_at,created_by,updated_at,updated_by) VALUES(?,?,?,?,?,?,?,?,'DRAFT',?,?,?,?)",
        (
            code,
            request_code,
            version,
            prev["code"] if prev else None,
            expiry.isoformat(),
            _json(service_data),
            clean_text(terms, "terms", max_len=2000, required=False),
            total,
            ts,
            actor,
            ts,
            actor,
        ),
    )
    conn.executemany(
        "INSERT INTO quote_lines(quote_code,description,quantity,unit_price_vnd,line_total_vnd) VALUES(?,?,?,?,?)",
        [(code, *line) for line in normalized],
    )
    write_audit(
        conn,
        actor=actor,
        action="QUOTE_CREATE",
        object_type="Quote",
        object_code=code,
        at=now,
        after={"request_code": request_code, "total_vnd": total, "version_no": version},
    )
    return _quote(conn, code)


def update_quote_draft(
    conn, code, *, version, valid_until, service_data, terms, lines, actor, now
):
    require_tx(conn)
    q = _row(conn, "quotes", code)
    check_version(q["row_version"], version)
    if q["status"] != "DRAFT":
        raise Conflict("Chỉ sửa được báo giá DRAFT.", code="INVALID_STATE")
    request = get_request(conn, q["request_code"])
    _validate_service_data(request["service_type"], service_data)
    before = {"total_vnd": q["total_vnd"], "valid_until": q["valid_until"]}
    conn.execute("DELETE FROM quote_lines WHERE quote_code=?", (code,))
    total = 0
    for line in lines:
        qty = int(line["quantity"])
        price = int(line["unit_price_vnd"])
        if qty <= 0 or price < 0:
            raise ValidationFailed("Dữ liệu dòng phí không hợp lệ.")
        amount = qty * price
        total += amount
        conn.execute(
            "INSERT INTO quote_lines(quote_code,description,quantity,unit_price_vnd,line_total_vnd) VALUES(?,?,?,?,?)",
            (
                code,
                clean_text(line["description"], "description", max_len=200),
                qty,
                price,
                amount,
            ),
        )
    if not lines or not total:
        raise ValidationFailed("Báo giá cần dòng có tổng dương.")
    expiry = date.fromisoformat(str(valid_until)[:10])
    if expiry < now.astimezone(SYSTEM_TZ).date():
        raise ValidationFailed(
            "Ngày hiệu lực không được ở quá khứ.", details={"field": "valid_until"}
        )
    conn.execute(
        "UPDATE quotes SET valid_until=?,service_data_json=?,terms=?,total_vnd=?,row_version=row_version+1,updated_at=?,updated_by=? WHERE code=?",
        (
            expiry.isoformat(),
            _json(service_data),
            terms,
            total,
            to_db(now),
            actor,
            code,
        ),
    )
    write_audit(
        conn,
        actor=actor,
        action="QUOTE_UPDATE",
        object_type="Quote",
        object_code=code,
        at=now,
        before=before,
        after={"total_vnd": total, "valid_until": expiry.isoformat()},
    )
    return _quote(conn, code)


def issue_quote(conn, code, *, actor, now):
    require_tx(conn)
    q = _row(conn, "quotes", code)
    if q["status"] != "DRAFT":
        raise Conflict("Chỉ phát hành báo giá DRAFT.", code="INVALID_STATE")
    if date.fromisoformat(q["valid_until"]) < now.astimezone(SYSTEM_TZ).date():
        raise Conflict("Báo giá nháp đã quá hạn.", code="QUOTE_EXPIRED")
    req = get_request(conn, q["request_code"])
    if not req["customer_code"]:
        raise Conflict("Yêu cầu chưa gắn khách hàng.", code="REQUEST_NOT_READY")
    if customer(conn, req["customer_code"])["status"] != "ACTIVE":
        raise Conflict("Khách hàng đã ngừng hoạt động.", code="CUSTOMER_INACTIVE")
    pending = conn.execute(
        "SELECT code FROM quotes WHERE request_code=? AND status='SENT'",
        (q["request_code"],),
    ).fetchone()
    if pending:
        conn.execute(
            "UPDATE quotes SET status='SUPERSEDED',updated_at=?,updated_by=? WHERE code=?",
            (to_db(now), actor, pending["code"]),
        )
    conn.execute(
        "UPDATE quotes SET status='SENT',updated_at=?,updated_by=? WHERE code=?",
        (to_db(now), actor, code),
    )
    conn.execute(
        "UPDATE service_requests SET status='QUOTING',version=version+1,updated_at=?,updated_by=? WHERE code=?",
        (to_db(now), actor, q["request_code"]),
    )
    _append_request_event(
        conn,
        q["request_code"],
        event_type="STATUS_CHANGED",
        content={"from": req["status"], "to": "QUOTING"},
        actor=actor,
        actual_at=now,
        recorded_at=now,
    )
    write_audit(
        conn,
        actor=actor,
        action="QUOTE_SENT",
        object_type="Quote",
        object_code=code,
        at=now,
        after={"status": "SENT"},
    )
    return _quote(conn, code)


def respond_quote_and_create_order(
    conn, code, *, accepted, channel, content, evidence, payment_due_date, actor, now
):
    require_tx(conn)
    q = _row(conn, "quotes", code)
    req = get_request(conn, q["request_code"])
    if q["status"] != "SENT":
        raise Conflict(
            "Chỉ chấp thuận/từ chối báo giá SENT chưa bị thay thế.",
            code="INVALID_STATE",
        )
    deadline = datetime.combine(
        date.fromisoformat(q["valid_until"]), time.max, tzinfo=SYSTEM_TZ
    )
    if now.astimezone(SYSTEM_TZ) > deadline:
        raise Conflict("Báo giá đã hết hiệu lực.", code="QUOTE_EXPIRED")
    if accepted and payment_due_date is None:
        raise ValidationFailed(
            "Cần ngày đến hạn thanh toán khi chấp thuận báo giá.",
            details={"field": "payment_due_date"},
        )
    response = {
        "accepted": accepted,
        "channel": clean_text(channel, "channel", max_len=50),
        "content": clean_text(content, "content", max_len=2000),
        "evidence": evidence,
        "recorded_at": to_db(now),
        "recorded_by": actor,
    }
    if evidence:
        linked_evidence = attachments.link(
            conn,
            evidence,
            owner_type="QuoteResponse",
            owner_code=code,
            actor=actor,
            now=now,
            min_count=1,
        )
        response["evidence"] = [item["code"] for item in linked_evidence]
    conn.execute(
        "UPDATE quotes SET status=?,response_json=?,updated_at=?,updated_by=? WHERE code=?",
        (
            "ACCEPTED" if accepted else "REJECTED",
            _json(response),
            to_db(now),
            actor,
            code,
        ),
    )
    write_audit(
        conn,
        actor=actor,
        action="QUOTE_RESPONSE",
        object_type="Quote",
        object_code=code,
        at=now,
        after={"status": "ACCEPTED" if accepted else "REJECTED"},
    )
    if not accepted:
        return {"quote": _quote(conn, code), "order": None}
    existing = conn.execute(
        "SELECT * FROM service_orders WHERE request_code=?", (req["code"],)
    ).fetchone()
    if existing:
        return {"quote": _quote(conn, code), "order": dict(existing)}
    cust = customer(conn, req["customer_code"])
    if cust["status"] != "ACTIVE":
        raise Conflict("Khách hàng đã ngừng hoạt động.", code="CUSTOMER_INACTIVE")
    due = date.fromisoformat(str(payment_due_date)[:10])
    order_code = next_code(conn, "ORD")
    ts = to_db(now)
    data = json.loads(q["service_data_json"])
    base = {
        "order_code": order_code,
        "customer_code": cust["code"],
        "actor": actor,
        "now": now,
    }
    if req["service_type"] == "TRANSPORT":
        op = assignments.create_transport_trip(
            conn,
            **base,
            cargo_weight_kg=data["cargo_weight_kg"],
            pickup_address=data["pickup_address"],
            delivery_address=data["delivery_address"],
            cargo_description=data.get("cargo_description", ""),
            pickup_from_warehouse=data.get("pickup_from_warehouse", False),
            committed_end=(
                datetime.fromisoformat(data["committed_end"])
                if data.get("committed_end")
                else None
            ),
        )
    elif req["service_type"] == "RENTAL":
        op = assignments.create_rental_record(
            conn,
            order_code=order_code,
            actor=actor,
            now=now,
            vehicle_type=data["vehicle_type"],
            min_capacity_kg=data["min_capacity_kg"],
            requested_start=datetime.fromisoformat(data["requested_start"]),
            requested_end=datetime.fromisoformat(data["requested_end"]),
        )
    else:
        op = warehouse.create_warehouse_lot(
            conn,
            **base,
            expected_in_at=(
                datetime.fromisoformat(data["expected_in_at"])
                if data.get("expected_in_at")
                else None
            ),
            expected_out_at=(
                datetime.fromisoformat(data["expected_out_at"])
                if data.get("expected_out_at")
                else None
            ),
            storage_notes=data.get("storage_notes", ""),
            lines=data.get("lines", []),
        )
    customer_snapshot = {
        field: cust[field]
        for field in (
            "code",
            "customer_type",
            "name",
            "contact_name",
            "phone",
            "email",
            "address",
        )
    }
    conn.execute(
        "INSERT INTO service_orders(code,request_code,quote_code,customer_code,service_type,service_snapshot_json,customer_snapshot_json,price_snapshot_vnd,operational_code,status,payment_due_date,created_at,created_by,updated_at,updated_by) "
        "VALUES(?,?,?,?,?,?,?,?,?,'CONFIRMED',?,?,?,?,?)",
        (
            order_code,
            req["code"],
            code,
            cust["code"],
            req["service_type"],
            _json(data),
            _json(customer_snapshot),
            q["total_vnd"],
            op["code"],
            due.isoformat(),
            ts,
            actor,
            ts,
            actor,
        ),
    )
    conn.execute(
        "UPDATE service_requests SET status='CONFIRMED',version=version+1,updated_at=?,updated_by=? WHERE code=?",
        (ts, actor, req["code"]),
    )
    _append_request_event(
        conn,
        req["code"],
        event_type="STATUS_CHANGED",
        content={"from": req["status"], "to": "CONFIRMED"},
        actor=actor,
        actual_at=now,
        recorded_at=now,
    )
    order = _row(conn, "service_orders", order_code)
    write_audit(
        conn,
        actor=actor,
        action="ORDER_CREATE",
        object_type="ServiceOrder",
        object_code=order_code,
        at=now,
        after={
            "quote_code": code,
            "operational_code": op["code"],
            "status": "CONFIRMED",
        },
    )
    return {"quote": _quote(conn, code), "order": order}


def order_balance(conn, order_code):
    o = _row(conn, "service_orders", order_code)
    charges = conn.execute(
        "SELECT COALESCE(SUM(amount_vnd),0) FROM charges WHERE order_code=? AND status='APPROVED'",
        (order_code,),
    ).fetchone()[0]
    paid = conn.execute(
        "SELECT COALESCE(SUM(CASE WHEN reversal_of IS NULL THEN amount_vnd ELSE -amount_vnd END),0) FROM payments WHERE order_code=?",
        (order_code,),
    ).fetchone()[0]
    due = 0 if o["status"] == "CANCELLED" else o["price_snapshot_vnd"] + charges
    paid = max(0, paid)
    remain = max(0, due - paid)
    state = (
        "NOT_APPLICABLE"
        if o["status"] == "CANCELLED"
        else "UNPAID" if paid == 0 else "PAID" if paid == due else "PARTIALLY_PAID"
    )
    return {
        "order_code": order_code,
        "order_status": o["status"],
        "quoted_vnd": o["price_snapshot_vnd"],
        "approved_charges_vnd": charges,
        "amount_due_vnd": due,
        "amount_paid_vnd": paid,
        "balance_vnd": remain,
        "payment_status": state,
        "payment_due_date": o["payment_due_date"],
    }


def order_detail(conn, code, *, now):
    order = _row(conn, "service_orders", code)
    order["service_snapshot"] = json.loads(order.pop("service_snapshot_json"))
    order["customer_snapshot"] = json.loads(order.pop("customer_snapshot_json"))
    order["balance"] = order_balance(conn, code)
    order["operational_record"] = {
        "TRANSPORT": lambda: assignments.get_trip(conn, order["operational_code"], now),
        "RENTAL": lambda: assignments.get_rental(conn, order["operational_code"], now),
        "WAREHOUSE": lambda: warehouse.get_lot(conn, order["operational_code"], now),
    }[order["service_type"]]()
    order["charges"] = [
        dict(row)
        for row in conn.execute(
            "SELECT * FROM charges WHERE order_code=? ORDER BY created_at,id", (code,)
        )
    ]
    order["payments"] = [
        dict(row)
        for row in conn.execute(
            "SELECT * FROM payments WHERE order_code=? ORDER BY created_at,id", (code,)
        )
    ]
    return order


def propose_charge(conn, order_code, *, amount_vnd, reason, evidence, actor, now):
    require_tx(conn)
    o = _row(conn, "service_orders", order_code)
    if o["status"] in ("COMPLETED", "CANCELLED"):
        raise Conflict(
            "Không thể đề nghị phụ phí sau khi đơn kết thúc.", code="INVALID_STATE"
        )
    if amount_vnd <= 0:
        raise ValidationFailed(
            "Phụ phí phải lớn hơn 0.", details={"field": "amount_vnd"}
        )
    code = next_code(conn, "CHG")
    linked_evidence = attachments.link(
        conn,
        evidence,
        owner_type="Charge",
        owner_code=code,
        actor=actor,
        now=now,
        min_count=1,
    )
    conn.execute(
        "INSERT INTO charges(code,order_code,amount_vnd,reason,evidence_json,status,created_at,created_by) VALUES(?,?,?,?,?,'PENDING',?,?)",
        (
            code,
            order_code,
            amount_vnd,
            clean_text(reason, "reason", max_len=2000),
            _json([item["code"] for item in linked_evidence]),
            to_db(now),
            actor,
        ),
    )
    write_audit(
        conn,
        actor=actor,
        action="CHARGE_PROPOSE",
        object_type="Charge",
        object_code=code,
        at=now,
        after={"order_code": order_code, "amount_vnd": amount_vnd, "status": "PENDING"},
    )
    return dict(conn.execute("SELECT * FROM charges WHERE code=?", (code,)).fetchone())


def decide_charge(conn, code, *, approved, decision_note, actor, now):
    require_tx(conn)
    c = _row(conn, "charges", code)
    if c["status"] != "PENDING":
        raise Conflict("Phụ phí đã được xử lý.", code="INVALID_STATE")
    status = "APPROVED" if approved else "REJECTED"
    conn.execute(
        "UPDATE charges SET status=?,decision_note=?,decided_by=?,decided_at=? WHERE code=?",
        (
            status,
            clean_text(decision_note, "decision_note", max_len=2000, required=False),
            actor,
            to_db(now),
            code,
        ),
    )
    write_audit(
        conn,
        actor=actor,
        action="CHARGE_DECISION",
        object_type="Charge",
        object_code=code,
        at=now,
        before={"status": "PENDING"},
        after={"status": status},
        reason=decision_note,
    )
    return dict(conn.execute("SELECT * FROM charges WHERE code=?", (code,)).fetchone())


def record_payment(
    conn, order_code, *, amount_vnd, occurred_at, method, reference, note, actor, now
):
    require_tx(conn)
    o = _row(conn, "service_orders", order_code)
    if o["status"] == "CANCELLED":
        raise Conflict("Không thu tiền cho đơn đã hủy.", code="ORDER_CANCELLED")
    if method not in ("CASH", "BANK_TRANSFER") or amount_vnd <= 0:
        raise ValidationFailed("Thông tin khoản thu không hợp lệ.")
    if method == "BANK_TRANSFER" and not reference:
        raise ValidationFailed(
            "Chuyển khoản cần tham chiếu chứng từ.", details={"field": "reference"}
        )
    if (
        reference
        and conn.execute(
            "SELECT 1 FROM payments WHERE order_code=? AND reference=? AND reversal_of IS NULL",
            (order_code, reference),
        ).fetchone()
    ):
        raise Conflict(
            "Tham chiếu chuyển khoản này đã được ghi nhận trên đơn.",
            code="PAYMENT_REFERENCE_EXISTS",
        )
    b = order_balance(conn, order_code)
    if amount_vnd > b["balance_vnd"]:
        raise Conflict(
            "Số tiền vượt số còn phải thu.",
            code="PAYMENT_EXCEEDS_BALANCE",
            details={"balance_vnd": b["balance_vnd"]},
        )
    code = next_code(conn, "PAY")
    conn.execute(
        "INSERT INTO payments(code,order_code,amount_vnd,occurred_at,method,reference,note,status,created_at,created_by) VALUES(?,?,?,?,?,?,?,'POSTED',?,?)",
        (
            code,
            order_code,
            amount_vnd,
            to_db(occurred_at),
            method,
            reference,
            clean_text(note, "note", max_len=2000, required=False),
            to_db(now),
            actor,
        ),
    )
    write_audit(
        conn,
        actor=actor,
        action="PAYMENT_POST",
        object_type="Payment",
        object_code=code,
        at=now,
        after={"order_code": order_code, "amount_vnd": amount_vnd},
    )
    return {
        "payment": _row(conn, "payments", code),
        "balance": order_balance(conn, order_code),
    }


def reverse_payment(conn, code, *, reason, actor, now):
    require_tx(conn)
    p = _row(conn, "payments", code)
    if p["reversal_of"] is not None or p["status"] != "POSTED":
        raise Conflict("Chỉ đảo được khoản thu POSTED gốc.", code="INVALID_STATE")
    if conn.execute("SELECT 1 FROM payments WHERE reversal_of=?", (code,)).fetchone():
        raise Conflict("Khoản thu đã được đảo.", code="PAYMENT_ALREADY_REVERSED")
    rev = next_code(conn, "PAY")
    conn.execute(
        "INSERT INTO payments(code,order_code,amount_vnd,occurred_at,method,reference,note,reversal_of,reason,status,created_at,created_by) VALUES(?,?,?,?,?,?,?, ?,?,'POSTED',?,?)",
        (
            rev,
            p["order_code"],
            p["amount_vnd"],
            to_db(now),
            p["method"],
            p["reference"],
            "Đảo khoản thu " + code,
            code,
            clean_text(reason, "reason", max_len=2000),
            to_db(now),
            actor,
        ),
    )
    conn.execute("UPDATE payments SET status='REVERSED' WHERE code=?", (code,))
    write_audit(
        conn,
        actor=actor,
        action="PAYMENT_REVERSE",
        object_type="Payment",
        object_code=rev,
        at=now,
        before={"source": code},
        after={"amount_vnd": p["amount_vnd"]},
        reason=reason,
    )
    return {
        "reversal": _row(conn, "payments", rev),
        "balance": order_balance(conn, p["order_code"]),
    }


def cancel_order(conn, code, *, reason, actor, now):
    require_tx(conn)
    o = _row(conn, "service_orders", code)
    if o["status"] != "CONFIRMED":
        raise Conflict(
            "Chỉ đơn chưa bắt đầu thực hiện mới có thể hủy.", code="NOT_CANCELLABLE"
        )
    if (
        _pending_count(conn, code)
        or conn.execute(
            "SELECT 1 FROM charges WHERE order_code=? AND status='APPROVED' LIMIT 1",
            (code,),
        ).fetchone()
    ):
        raise Conflict(
            "Đơn còn phụ phí cần xử lý; không thể hủy.", code="CHARGES_EXIST"
        )
    if conn.execute(
        "SELECT 1 FROM payments WHERE order_code=? AND reversal_of IS NULL AND status='POSTED'",
        (code,),
    ).fetchone():
        raise Conflict(
            "Đơn đã có khoản thu hiệu lực; không thể hủy.", code="NOT_CANCELLABLE"
        )
    assignments.cancel_operational_record(
        conn,
        source_type={
            "TRANSPORT": "TRANSPORT",
            "RENTAL": "RENTAL",
            "WAREHOUSE": "WAREHOUSE",
        }[o["service_type"]],
        source_code=o["operational_code"],
        reason=reason,
        actor=actor,
        now=now,
    )
    conn.execute(
        "UPDATE service_orders SET status='CANCELLED',version=version+1,updated_at=?,updated_by=?,cancelled_at=?,cancelled_by=?,cancel_reason=? WHERE code=?",
        (to_db(now), actor, to_db(now), actor, reason, code),
    )
    write_audit(
        conn,
        actor=actor,
        action="ORDER_CANCEL",
        object_type="ServiceOrder",
        object_code=code,
        at=now,
        before={"status": o["status"]},
        after={"status": "CANCELLED"},
        reason=reason,
    )
    return _row(conn, "service_orders", code)


def list_orders(
    conn,
    *,
    customer_code=None,
    service_type=None,
    status=None,
    payment_status=None,
    due_from=None,
    due_to=None,
    overdue_only=False,
    q=None,
    limit=50,
    offset=0,
    now,
):
    local_today = now.astimezone(SYSTEM_TZ).date().isoformat()
    filters = []
    params = []
    for column, value in (
        ("customer_code", customer_code),
        ("service_type", service_type),
        ("status", status),
    ):
        if value is not None:
            filters.append(f"{column}=?")
            params.append(value)
    if due_from is not None:
        filters.append("payment_due_date>=?")
        params.append(date.fromisoformat(str(due_from)[:10]).isoformat())
    if due_to is not None:
        filters.append("payment_due_date<=?")
        params.append(date.fromisoformat(str(due_to)[:10]).isoformat())
    if q:
        filters.append("(lower(code) LIKE ? OR lower(customer_code) LIKE ?)")
        params.extend((f"%{q.lower()}%", f"%{q.lower()}%"))

    having = []
    if payment_status:
        having.append("payment_status=?")
        params.append(payment_status)
    if overdue_only:
        having.append("overdue=1")
    where_sql = " WHERE " + " AND ".join(filters) if filters else ""
    result_filter_sql = " WHERE " + " AND ".join(having) if having else ""
    financial_query = """
        SELECT o.*,
            COALESCE(charges.approved_charges_vnd, 0) AS approved_charges_vnd,
            COALESCE(payments.amount_paid_vnd, 0) AS amount_paid_vnd,
            CASE WHEN o.status='CANCELLED' THEN 0
                 ELSE o.price_snapshot_vnd + COALESCE(charges.approved_charges_vnd, 0)
            END AS amount_due_vnd,
            CASE WHEN o.status='CANCELLED' THEN 0
                 ELSE max(0, o.price_snapshot_vnd
                    + COALESCE(charges.approved_charges_vnd, 0)
                    - COALESCE(payments.amount_paid_vnd, 0))
            END AS balance_vnd,
            CASE WHEN o.status='CANCELLED' THEN 'NOT_APPLICABLE'
                 WHEN COALESCE(payments.amount_paid_vnd, 0)=0 THEN 'UNPAID'
                 WHEN COALESCE(payments.amount_paid_vnd, 0) >=
                    o.price_snapshot_vnd + COALESCE(charges.approved_charges_vnd, 0)
                    THEN 'PAID'
                 ELSE 'PARTIALLY_PAID'
            END AS payment_status,
            CASE WHEN o.status<>'CANCELLED' AND o.payment_due_date < ?
                AND max(0, o.price_snapshot_vnd
                    + COALESCE(charges.approved_charges_vnd, 0)
                    - COALESCE(payments.amount_paid_vnd, 0)) > 0
                THEN 1 ELSE 0 END AS overdue
        FROM service_orders AS o
        LEFT JOIN (
            SELECT order_code, SUM(amount_vnd) AS approved_charges_vnd
            FROM charges WHERE status='APPROVED' GROUP BY order_code
        ) AS charges ON charges.order_code=o.code
        LEFT JOIN (
            SELECT order_code,
                SUM(CASE WHEN reversal_of IS NULL THEN amount_vnd ELSE -amount_vnd END)
                    AS amount_paid_vnd
            FROM payments GROUP BY order_code
        ) AS payments ON payments.order_code=o.code
    """
    query_params = [local_today, *params]
    summary_row = conn.execute(
        "SELECT COUNT(*) AS total, "
        "COALESCE(SUM(amount_due_vnd),0) AS amount_due_vnd, "
        "COALESCE(SUM(amount_paid_vnd),0) AS amount_paid_vnd, "
        "COALESCE(SUM(balance_vnd),0) AS balance_vnd "
        f"FROM ({financial_query}{where_sql}) AS financial{result_filter_sql}",
        query_params,
    ).fetchone()
    total = summary_row["total"]
    rows = conn.execute(
        f"SELECT * FROM ({financial_query}{where_sql}) AS financial"
        f"{result_filter_sql} ORDER BY created_at DESC LIMIT ? OFFSET ?",
        [*query_params, limit, offset],
    ).fetchall()
    items = []
    for row in rows:
        order = dict(row)
        balance = {
            "order_code": order["code"],
            "order_status": order["status"],
            "quoted_vnd": order["price_snapshot_vnd"],
            "approved_charges_vnd": order["approved_charges_vnd"],
            "amount_due_vnd": order["amount_due_vnd"],
            "amount_paid_vnd": max(0, order["amount_paid_vnd"]),
            "balance_vnd": order["balance_vnd"],
            "payment_status": order["payment_status"],
            "payment_due_date": order["payment_due_date"],
        }
        overdue = bool(order.pop("overdue"))
        for key in (
            "approved_charges_vnd",
            "amount_paid_vnd",
            "amount_due_vnd",
            "balance_vnd",
            "payment_status",
        ):
            order.pop(key)
        items.append({**order, "balance": balance, "overdue": overdue})
    return {
        "items": items,
        "limit": limit,
        "offset": offset,
        "total": total,
        "summary": {
            "amount_due_vnd": summary_row["amount_due_vnd"],
            "amount_paid_vnd": max(0, summary_row["amount_paid_vnd"]),
            "balance_vnd": summary_row["balance_vnd"],
        },
    }


def update_due_date(conn, code, *, due_date, reason, version, actor, now):
    require_tx(conn)
    o = _row(conn, "service_orders", code)
    check_version(o["version"], version)
    if o["status"] == "CANCELLED":
        raise Conflict(
            "Không cập nhật hạn thanh toán của đơn đã hủy.", code="ORDER_CANCELLED"
        )
    d = date.fromisoformat(str(due_date)[:10]).isoformat()
    conn.execute(
        "UPDATE service_orders SET payment_due_date=?,version=version+1,updated_at=?,updated_by=? WHERE code=?",
        (d, to_db(now), actor, code),
    )
    write_audit(
        conn,
        actor=actor,
        action="PAYMENT_DUE_CHANGE",
        object_type="ServiceOrder",
        object_code=code,
        at=now,
        before={"payment_due_date": o["payment_due_date"]},
        after={"payment_due_date": d},
        reason=reason,
    )
    return order_balance(conn, code)


def update_order_contact_note(conn, code, *, contact_note, version, actor, now):
    """Update the operational contact note without changing order snapshots."""
    require_tx(conn)
    order = _row(conn, "service_orders", code)
    check_version(order["version"], version)
    if order["status"] == "CANCELLED":
        raise Conflict("Không cập nhật ghi chú đơn đã hủy.", code="ORDER_CANCELLED")
    note = clean_text(contact_note, "contact_note", max_len=2000, required=False)
    conn.execute(
        "UPDATE service_orders SET contact_note=?,version=version+1,updated_at=?,updated_by=? "
        "WHERE code=? AND version=?",
        (note, to_db(now), actor, code, version),
    )
    write_audit(
        conn,
        actor=actor,
        action="ORDER_CONTACT_NOTE_UPDATE",
        object_type="ServiceOrder",
        object_code=code,
        at=now,
        before={"contact_note": order["contact_note"]},
        after={"contact_note": note},
    )
    return _row(conn, "service_orders", code)


def _pending_count(conn, order_code):
    return conn.execute(
        "SELECT COUNT(*) FROM charges WHERE order_code=? AND status='PENDING'",
        (order_code,),
    ).fetchone()[0]


def _operation_status(conn, order_code, status, at):
    o = conn.execute(
        "SELECT * FROM service_orders WHERE code=?", (order_code,)
    ).fetchone()
    if o is None:
        return
    if o["status"] in ("CANCELLED", "COMPLETED"):
        return
    if o["status"] == status:
        return
    if status == "COMPLETED" and _pending_count(conn, order_code):
        raise Conflict("Đơn còn phụ phí chờ duyệt.", code="PENDING_CHARGES")
    conn.execute(
        "UPDATE service_orders SET status=?,version=version+1,updated_at=? WHERE code=?",
        (status, to_db(at), order_code),
    )
    write_audit(
        conn,
        actor="system",
        action="ORDER_" + status,
        object_type="ServiceOrder",
        object_code=order_code,
        at=at,
        before={"status": o["status"]},
        after={"status": status},
    )


def bind_integrations():
    hooks.pending_charge_count = _pending_count
    hooks.operation_started = lambda c, o, s, a: _operation_status(
        c, o, "IN_PROGRESS", a
    )
    hooks.operation_completed = lambda c, o, s, a: _operation_status(
        c, o, "COMPLETED", a
    )


bind_integrations()
