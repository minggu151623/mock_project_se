"""HTTP API kho trung chuyển (WHS-01→06)."""

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import Field

from . import warehouse
from .api_common import (Body, Limit, NameText, NoteText, Offset, ReasonText, get_conn, idempotent_write, now_of,
                         role_dependency)
from .auth import APPROVER_ROLES, WAREHOUSE_READ_ROLES, WAREHOUSE_WRITE_ROLES, Principal
from .db import write_tx

router = APIRouter(prefix="/warehouse")
reader = role_dependency(WAREHOUSE_READ_ROLES)
writer = role_dependency(WAREHOUSE_WRITE_ROLES)
approver = role_dependency(APPROVER_ROLES)
IdemKey = Annotated[str | None, Header(alias="Idempotency-Key")]


class LineCreate(Body):
    item_code: str = Field(min_length=1, max_length=50)
    item_name: NameText
    unit_code: str = Field(min_length=1, max_length=20)
    expected_qty: int = Field(gt=0)


class LineUpdate(Body):
    version: int
    item_code: str | None = Field(None, min_length=1, max_length=50)
    item_name: str | None = Field(None, min_length=1, max_length=200)
    unit_code: str | None = Field(None, min_length=1, max_length=20)
    expected_qty: int | None = Field(None, gt=0)


class VersionOnly(Body):
    version: int


class DocLine(Body):
    lot_line_id: int
    quantity: int = Field(gt=0)
    note: NoteText | None = None


class ReceiptDraft(Body):
    occurred_at: datetime
    counterpart_name: NameText
    condition_note: NoteText = ""
    discrepancy_reason: NoteText = ""
    lines: list[DocLine] = Field(min_length=1, max_length=200)


class IssueDraft(Body):
    occurred_at: datetime
    counterpart_name: NameText
    destination: str = Field(min_length=1, max_length=500)
    transport_trip_code: str | None = Field(None, max_length=50)
    lines: list[DocLine] = Field(min_length=1, max_length=200)


class DraftUpdate(Body):
    version: int
    occurred_at: datetime
    counterpart_name: NameText
    condition_note: NoteText = ""
    discrepancy_reason: NoteText = ""
    destination: str = Field("", max_length=500)
    transport_trip_code: str | None = Field(None, max_length=50)
    lines: list[DocLine] = Field(min_length=1, max_length=200)


class CancelDraft(Body):
    version: int
    reason: ReasonText


class AdjustLine(Body):
    source_line_id: int
    quantity: int = Field(gt=0)


class AdjustmentRequest(Body):
    reason: ReasonText
    lines: list[AdjustLine] = Field(min_length=1, max_length=200)
    attachment_codes: list[str] = Field(default_factory=list, max_length=20)


class Decision(Body):
    decision_note: NoteText | None = None


class Rejection(Body):
    decision_note: ReasonText


class CloseLot(Body):
    version: int
    no_more_receipts: bool


def _lines(body) -> list[dict]:
    return [line.model_dump() for line in body.lines]


# ---------- Lô (WHS-01, WHS-04) ----------

@router.get("/lots")
def list_lots(
    request: Request, q: str | None = None, customer_code: str | None = None,
    status: Literal["WAITING_RECEIPT", "IN_STORAGE", "COMPLETED", "CANCELLED"] | None = None,
    received_from: datetime | None = None, received_to: datetime | None = None, limit: Limit = 50,
    offset: Offset = 0, _: Principal = Depends(reader), conn=Depends(get_conn),
):
    return warehouse.list_lots(conn, now=now_of(request), q=q, customer_code=customer_code, status=status,
                               received_from=received_from, received_to=received_to, limit=limit, offset=offset)


@router.get("/lots/{lot_code}")
def get_lot(lot_code: str, request: Request, _: Principal = Depends(reader), conn=Depends(get_conn)):
    return warehouse.get_lot(conn, lot_code, now_of(request))


@router.get("/lots/{lot_code}/documents")
def lot_documents(lot_code: str, _: Principal = Depends(reader), conn=Depends(get_conn)):
    return {"items": warehouse.list_documents(conn, lot_code)}


@router.get("/lots/{lot_code}/transport-candidates")
def transport_candidates(lot_code: str, _: Principal = Depends(reader), conn=Depends(get_conn)):
    return {"items": warehouse.transport_candidates(conn, lot_code)}


@router.post("/lots/{lot_code}/lines", status_code=201)
def add_line(lot_code: str, body: LineCreate, request: Request, p: Principal = Depends(writer),
             conn=Depends(get_conn)):
    with write_tx(conn):
        return warehouse.add_line(conn, lot_code, **body.model_dump(), actor=p.username, now=now_of(request))


@router.patch("/lots/{lot_code}/lines/{line_id}")
def update_line(lot_code: str, line_id: int, body: LineUpdate, request: Request, p: Principal = Depends(writer),
                conn=Depends(get_conn)):
    data = body.model_dump(exclude={"version"})
    with write_tx(conn):
        return warehouse.update_line(conn, lot_code, line_id, expected_version=body.version, **data,
                                     actor=p.username, now=now_of(request))


@router.post("/lots/{lot_code}/lines/{line_id}/remove")
def remove_line(lot_code: str, line_id: int, body: VersionOnly, request: Request, p: Principal = Depends(writer),
                conn=Depends(get_conn)):
    with write_tx(conn):
        return warehouse.remove_line(conn, lot_code, line_id, expected_version=body.version, actor=p.username,
                                     now=now_of(request))


@router.post("/lots/{lot_code}/close")
def close_lot(lot_code: str, body: CloseLot, request: Request, p: Principal = Depends(writer),
              conn=Depends(get_conn)):
    with write_tx(conn):
        return warehouse.close_lot(conn, lot_code, expected_version=body.version,
                                   no_more_receipts=body.no_more_receipts, actor=p.username, now=now_of(request))


# ---------- Phiếu nhập/xuất (WHS-02, WHS-03) ----------

@router.post("/lots/{lot_code}/receipts", status_code=201)
def create_receipt(lot_code: str, body: ReceiptDraft, request: Request, response: Response,
                   idempotency_key: IdemKey = None, p: Principal = Depends(writer), conn=Depends(get_conn)):
    now = now_of(request)
    data = body.model_dump(exclude={"lines"})
    return idempotent_write(conn, response, key=idempotency_key, principal=p, operation=f"wh.receipt:{lot_code}",
                            body=body, now=now, status_code=201,
                            action=lambda: warehouse.create_receipt(conn, lot_code, **data, lines=_lines(body),
                                                                    actor=p.username, now=now))


@router.post("/lots/{lot_code}/issues", status_code=201)
def create_issue(lot_code: str, body: IssueDraft, request: Request, response: Response,
                 idempotency_key: IdemKey = None, p: Principal = Depends(writer), conn=Depends(get_conn)):
    now = now_of(request)
    data = body.model_dump(exclude={"lines"})
    return idempotent_write(conn, response, key=idempotency_key, principal=p, operation=f"wh.issue:{lot_code}",
                            body=body, now=now, status_code=201,
                            action=lambda: warehouse.create_issue(conn, lot_code, **data, lines=_lines(body),
                                                                  actor=p.username, now=now))


@router.get("/documents/{doc_code}")
def get_document(doc_code: str, _: Principal = Depends(reader), conn=Depends(get_conn)):
    return warehouse.get_document(conn, doc_code)


@router.put("/documents/{doc_code}")
def update_draft(doc_code: str, body: DraftUpdate, request: Request, p: Principal = Depends(writer),
                 conn=Depends(get_conn)):
    data = body.model_dump(exclude={"version", "lines"})
    with write_tx(conn):
        return warehouse.update_draft(conn, doc_code, expected_version=body.version, **data, lines=_lines(body),
                                      actor=p.username, now=now_of(request))


@router.post("/documents/{doc_code}/post")
def post_document(doc_code: str, request: Request, p: Principal = Depends(writer), conn=Depends(get_conn)):
    with write_tx(conn):
        return warehouse.post_document(conn, doc_code, actor=p.username, now=now_of(request))


@router.post("/documents/{doc_code}/cancel")
def cancel_draft(doc_code: str, body: CancelDraft, request: Request, p: Principal = Depends(writer),
                 conn=Depends(get_conn)):
    with write_tx(conn):
        return warehouse.cancel_draft(conn, doc_code, expected_version=body.version, reason=body.reason,
                                      actor=p.username, now=now_of(request))


# ---------- Điều chỉnh (WHS-05) ----------

@router.post("/documents/{doc_code}/adjustments", status_code=201)
def request_adjustment(doc_code: str, body: AdjustmentRequest, request: Request, response: Response,
                       idempotency_key: IdemKey = None, p: Principal = Depends(writer), conn=Depends(get_conn)):
    now = now_of(request)
    return idempotent_write(conn, response, key=idempotency_key, principal=p, operation=f"wh.adjust:{doc_code}",
                            body=body, now=now, status_code=201,
                            action=lambda: warehouse.request_adjustment(
                                conn, doc_code, lines=[l.model_dump() for l in body.lines], reason=body.reason,
                                attachment_codes=body.attachment_codes, actor=p.username, now=now))


@router.get("/approvals")
def approval_queue(limit: Limit = 50, offset: Offset = 0, _: Principal = Depends(approver), conn=Depends(get_conn)):
    return warehouse.approval_queue(conn, limit=limit, offset=offset)


@router.post("/documents/{doc_code}/approve")
def approve(doc_code: str, body: Decision, request: Request, p: Principal = Depends(approver),
            conn=Depends(get_conn)):
    with write_tx(conn):
        return warehouse.approve_adjustment(conn, doc_code, decision_note=body.decision_note, actor=p.username,
                                            now=now_of(request))


@router.post("/documents/{doc_code}/reject")
def reject(doc_code: str, body: Rejection, request: Request, p: Principal = Depends(approver),
           conn=Depends(get_conn)):
    with write_tx(conn):
        return warehouse.reject_adjustment(conn, doc_code, decision_note=body.decision_note, actor=p.username,
                                           now=now_of(request))
