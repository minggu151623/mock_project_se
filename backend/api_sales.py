"""HTTP API BE1: kinh doanh, đơn dịch vụ, phụ phí và công nợ."""

from datetime import date, datetime
from typing import Literal
from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import Field
from . import errors, sales
from .sales_pdf import render_quote_pdf
from .api_common import (
    Body,
    Limit,
    Offset,
    ReasonText,
    get_conn,
    idempotent_write,
    now_of,
    role_dependency,
)
from .auth import Principal
from .db import write_tx

router = APIRouter(prefix="/sales")
sal = role_dependency(frozenset({"SAL"}))
read_sales = role_dependency(frozenset({"SAL", "MGR"}))
fin = role_dependency(frozenset({"FIN"}))
read_fin = role_dependency(frozenset({"FIN", "SAL", "MGR"}))
mgr = role_dependency(frozenset({"MGR"}))
any_charge = role_dependency(frozenset({"SAL", "DSP", "WHS"}))


class CustomerIn(Body):
    customer_type: Literal["INDIVIDUAL", "BUSINESS"]
    name: str = Field(min_length=1, max_length=200)
    contact_name: str = Field(min_length=1, max_length=200)
    phone: str = Field(min_length=1, max_length=30)
    email: str | None = None
    address: str | None = None
    duplicate_reason: str | None = None


class Version(Body):
    version: int = Field(ge=1)


class CustomerPatch(Version):
    name: str | None = None
    contact_name: str | None = None
    phone: str | None = None
    email: str | None = None
    address: str | None = None
    duplicate_reason: str | None = None


class Status(Version):
    status: Literal["ACTIVE", "INACTIVE"]


class RequestIn(Body):
    service_type: Literal["TRANSPORT", "RENTAL", "WAREHOUSE"]
    contact: dict
    service_data: dict


class RequestPatch(Version):
    status: Literal["NEW", "CLARIFYING", "QUOTING", "CLOSED_LOST"] | None = None
    customer_code: str | None = None
    assigned_to: str | None = None
    clarification: str | None = None
    reason: str | None = None


class QuoteLine(Body):
    description: str = Field(min_length=1, max_length=200)
    quantity: int = Field(gt=0)
    unit_price_vnd: int = Field(ge=0)


class QuoteIn(Body):
    request_code: str
    valid_until: date
    service_data: dict
    terms: str = ""
    lines: list[QuoteLine] = Field(min_length=1, max_length=100)


class QuoteUpdate(Body):
    version: int = Field(ge=1)
    valid_until: date
    service_data: dict
    terms: str = ""
    lines: list[QuoteLine] = Field(min_length=1, max_length=100)


class QuoteResponse(Body):
    accepted: bool
    channel: str = Field(min_length=1, max_length=50)
    content: str = Field(min_length=1, max_length=2000)
    evidence: list[str] = Field(default_factory=list, max_length=10)
    payment_due_date: date | None = None


class ChargeIn(Body):
    amount_vnd: int = Field(gt=0)
    reason: ReasonText
    evidence: list[str] = Field(min_length=1, max_length=10)


class ChargeDecision(Body):
    approved: bool
    decision_note: str = ""


class PaymentIn(Body):
    amount_vnd: int = Field(gt=0)
    occurred_at: datetime
    method: Literal["CASH", "BANK_TRANSFER"]
    reference: str | None = None
    note: str = ""


class ReverseIn(Body):
    reason: ReasonText


class CancelIn(Body):
    reason: ReasonText


class DueDateIn(Version):
    due_date: date
    reason: ReasonText


class ContactNoteIn(Version):
    contact_note: str = Field(max_length=2000)


class ContactLogIn(Body):
    channel: Literal["PHONE", "EMAIL", "IN_PERSON"]
    content: ReasonText
    follow_up: str = ""
    actual_at: datetime


class RequestEventCorrectionIn(Body):
    corrected_content: ReasonText
    reason: ReasonText


class TokenRotateIn(Body):
    verification: ReasonText
    reason: ReasonText


@router.post("/customers", status_code=201)
def create_customer(
    b: CustomerIn, req: Request, p: Principal = Depends(sal), c=Depends(get_conn)
):
    with write_tx(c):
        return sales.create_customer(
            c, **b.model_dump(), actor=p.username, now=now_of(req)
        )


@router.get("/customers")
def customers(
    q: str | None = None,
    status: Literal["ACTIVE", "INACTIVE"] | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
    _: Principal = Depends(read_sales),
    c=Depends(get_conn),
):
    return sales.list_customers(c, q=q, status=status, limit=limit, offset=offset)


@router.get("/customers/{code}")
def customer(code: str, _: Principal = Depends(read_sales), c=Depends(get_conn)):
    return sales.customer_detail(c, code)


@router.patch("/customers/{code}")
def patch_customer(
    code: str,
    b: CustomerPatch,
    req: Request,
    p: Principal = Depends(sal),
    c=Depends(get_conn),
):
    with write_tx(c):
        return sales.update_customer(
            c,
            code,
            version=b.version,
            actor=p.username,
            now=now_of(req),
            **b.model_dump(exclude={"version"}, exclude_unset=True),
        )


@router.post("/customers/{code}/status")
def customer_status(
    code: str, b: Status, req: Request, p: Principal = Depends(sal), c=Depends(get_conn)
):
    with write_tx(c):
        return sales.set_customer_status(
            c,
            code,
            version=b.version,
            status=b.status,
            actor=p.username,
            now=now_of(req),
        )


@router.post("/requests", status_code=201)
def create_request(
    b: RequestIn, req: Request, p: Principal = Depends(sal), c=Depends(get_conn)
):
    with write_tx(c):
        return sales.create_request(
            c, **b.model_dump(), source="INTERNAL", actor=p.username, now=now_of(req)
        )


@router.get("/requests")
def requests(
    status: (
        Literal["NEW", "CLARIFYING", "QUOTING", "CONFIRMED", "CLOSED_LOST"] | None
    ) = None,
    service_type: Literal["TRANSPORT", "RENTAL", "WAREHOUSE"] | None = None,
    assigned_to: str | None = None,
    unassigned_only: bool = False,
    q: str | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
    _: Principal = Depends(read_sales),
    c=Depends(get_conn),
):
    return sales.list_requests(
        c,
        status=status,
        service_type=service_type,
        assigned_to=assigned_to,
        unassigned_only=unassigned_only,
        q=q,
        limit=limit,
        offset=offset,
    )


@router.get("/requests/{code}")
def request_detail(code: str, _: Principal = Depends(read_sales), c=Depends(get_conn)):
    request = sales.get_request(c, code)
    request.pop("public_token_hash", None)
    return request


@router.post("/requests/{code}/contacts")
def contact_log(
    code: str,
    b: ContactLogIn,
    req: Request,
    p: Principal = Depends(sal),
    c=Depends(get_conn),
):
    with write_tx(c):
        return sales.record_contact(
            c,
            code,
            channel=b.channel,
            content=b.content,
            follow_up=b.follow_up,
            actual_at=b.actual_at,
            actor=p.username,
            now=now_of(req),
        )


@router.post("/requests/{code}/events/{event_id}/correction")
def correct_request_event(
    code: str,
    event_id: int,
    b: RequestEventCorrectionIn,
    req: Request,
    p: Principal = Depends(sal),
    c=Depends(get_conn),
):
    with write_tx(c):
        return sales.correct_request_event(
            c,
            code,
            event_id,
            **b.model_dump(),
            actor=p.username,
            now=now_of(req),
        )


@router.post("/requests/{code}/rotate-token")
def rotate_token(
    code: str,
    b: TokenRotateIn,
    req: Request,
    p: Principal = Depends(sal),
    c=Depends(get_conn),
):
    with write_tx(c):
        return sales.rotate_request_token(
            c,
            code,
            verification=b.verification,
            reason=b.reason,
            actor=p.username,
            now=now_of(req),
        )


@router.patch("/requests/{code}")
def patch_request(
    code: str,
    b: RequestPatch,
    req: Request,
    p: Principal = Depends(sal),
    c=Depends(get_conn),
):
    with write_tx(c):
        return sales.update_request(
            c,
            code,
            version=b.version,
            actor=p.username,
            now=now_of(req),
            **b.model_dump(exclude={"version"}, exclude_unset=True),
        )


@router.post("/quotes", status_code=201)
def quote_create(
    b: QuoteIn, req: Request, p: Principal = Depends(sal), c=Depends(get_conn)
):
    with write_tx(c):
        return sales.create_quote(
            c, **b.model_dump(), actor=p.username, now=now_of(req)
        )


@router.get("/quotes/{code}")
def quote_get(
    code: str, req: Request, _: Principal = Depends(read_sales), c=Depends(get_conn)
):
    return sales.quote_detail(c, code, now=now_of(req))


@router.get("/quotes/{code}/pdf")
def quote_pdf(
    code: str,
    req: Request,
    _: Principal = Depends(read_sales),
    c=Depends(get_conn),
):
    quote = sales.quote_detail(c, code, now=now_of(req))
    if quote["status"] == "DRAFT":
        raise errors.Conflict(
            "Chỉ báo giá đã phát hành mới được xuất PDF.", code="INVALID_STATE"
        )
    return Response(
        content=render_quote_pdf(quote),
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{code}.pdf"'},
    )


@router.put("/quotes/{code}")
def quote_update(
    code: str,
    b: QuoteUpdate,
    req: Request,
    p: Principal = Depends(sal),
    c=Depends(get_conn),
):
    with write_tx(c):
        return sales.update_quote_draft(
            c,
            code,
            version=b.version,
            valid_until=b.valid_until,
            service_data=b.service_data,
            terms=b.terms,
            lines=[x.model_dump() for x in b.lines],
            actor=p.username,
            now=now_of(req),
        )


@router.post("/quotes/{code}/issue")
def quote_issue(
    code: str, req: Request, p: Principal = Depends(sal), c=Depends(get_conn)
):
    with write_tx(c):
        return sales.issue_quote(c, code, actor=p.username, now=now_of(req))


@router.post("/quotes/{code}/response")
def quote_response(
    code: str,
    b: QuoteResponse,
    req: Request,
    res: Response,
    key: str | None = Header(None, alias="Idempotency-Key"),
    p: Principal = Depends(sal),
    c=Depends(get_conn),
):
    now = now_of(req)
    return idempotent_write(
        c,
        res,
        key=key,
        principal=p,
        operation=f"quote.response:{code}",
        body=b,
        now=now,
        action=lambda: sales.respond_quote_and_create_order(
            c, code, **b.model_dump(), actor=p.username, now=now
        ),
    )


@router.get("/orders/{code}")
def order_get(
    code: str,
    req: Request,
    _: Principal = Depends(read_fin),
    c=Depends(get_conn),
):
    return sales.order_detail(c, code, now=now_of(req))


@router.get("/orders")
def orders(
    req: Request,
    customer_code: str | None = None,
    service_type: str | None = None,
    status: str | None = None,
    payment_status: str | None = None,
    due_from: date | None = None,
    due_to: date | None = None,
    overdue_only: bool = False,
    q: str | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
    _: Principal = Depends(read_fin),
    c=Depends(get_conn),
):
    return sales.list_orders(
        c,
        customer_code=customer_code,
        service_type=service_type,
        status=status,
        payment_status=payment_status,
        due_from=due_from,
        due_to=due_to,
        overdue_only=overdue_only,
        q=q,
        limit=limit,
        offset=offset,
        now=now_of(req),
    )


@router.patch("/orders/{code}/due-date")
def due_date(
    code: str,
    b: DueDateIn,
    req: Request,
    p: Principal = Depends(role_dependency(frozenset({"SAL", "FIN"}))),
    c=Depends(get_conn),
):
    with write_tx(c):
        return sales.update_due_date(
            c,
            code,
            due_date=b.due_date,
            reason=b.reason,
            version=b.version,
            actor=p.username,
            now=now_of(req),
        )


@router.patch("/orders/{code}/contact-note")
def order_contact_note(
    code: str,
    b: ContactNoteIn,
    req: Request,
    p: Principal = Depends(sal),
    c=Depends(get_conn),
):
    with write_tx(c):
        return sales.update_order_contact_note(
            c,
            code,
            contact_note=b.contact_note,
            version=b.version,
            actor=p.username,
            now=now_of(req),
        )


@router.post("/orders/{code}/cancel")
def cancel_order(
    code: str,
    b: CancelIn,
    req: Request,
    res: Response,
    key: str | None = Header(None, alias="Idempotency-Key"),
    p: Principal = Depends(mgr),
    c=Depends(get_conn),
):
    now = now_of(req)
    return idempotent_write(
        c,
        res,
        key=key,
        principal=p,
        operation=f"order.cancel:{code}",
        body=b,
        now=now,
        action=lambda: sales.cancel_order(
            c, code, reason=b.reason, actor=p.username, now=now
        ),
    )


@router.post("/orders/{code}/charges", status_code=201)
def charge_create(
    code: str,
    b: ChargeIn,
    req: Request,
    res: Response,
    key: str | None = Header(None, alias="Idempotency-Key"),
    p: Principal = Depends(any_charge),
    c=Depends(get_conn),
):
    sales.ensure_charge_role(c, code, p.roles)
    now = now_of(req)
    return idempotent_write(
        c,
        res,
        key=key,
        principal=p,
        operation=f"charge.propose:{code}",
        body=b,
        now=now,
        status_code=201,
        action=lambda: sales.propose_charge(
            c, code, **b.model_dump(), roles=p.roles, actor=p.username, now=now
        ),
    )


@router.post("/charges/{code}/decision")
def charge_decide(
    code: str,
    b: ChargeDecision,
    req: Request,
    res: Response,
    key: str | None = Header(None, alias="Idempotency-Key"),
    p: Principal = Depends(mgr),
    c=Depends(get_conn),
):
    now = now_of(req)
    return idempotent_write(
        c,
        res,
        key=key,
        principal=p,
        operation=f"charge.decision:{code}",
        body=b,
        now=now,
        action=lambda: sales.decide_charge(
            c, code, **b.model_dump(), actor=p.username, now=now
        ),
    )


@router.get("/orders/{code}/receivables")
def receivable(code: str, _: Principal = Depends(read_fin), c=Depends(get_conn)):
    return sales.order_balance(c, code)


@router.post("/orders/{code}/payments", status_code=201)
def payment_create(
    code: str,
    b: PaymentIn,
    req: Request,
    res: Response,
    key: str | None = Header(None, alias="Idempotency-Key"),
    p: Principal = Depends(fin),
    c=Depends(get_conn),
):
    now = now_of(req)
    return idempotent_write(
        c,
        res,
        key=key,
        principal=p,
        operation=f"payment.post:{code}",
        body=b,
        now=now,
        status_code=201,
        action=lambda: sales.record_payment(
            c, code, **b.model_dump(), actor=p.username, now=now
        ),
    )


@router.post("/payments/{code}/reverse")
def payment_reverse(
    code: str,
    b: ReverseIn,
    req: Request,
    res: Response,
    key: str | None = Header(None, alias="Idempotency-Key"),
    p: Principal = Depends(fin),
    c=Depends(get_conn),
):
    now = now_of(req)
    return idempotent_write(
        c,
        res,
        key=key,
        principal=p,
        operation=f"payment.reverse:{code}",
        body=b,
        now=now,
        action=lambda: sales.reverse_payment(
            c, code, **b.model_dump(), actor=p.username, now=now
        ),
    )
