"""HTTP API cho website công khai, gửi yêu cầu dịch vụ, tra cứu tiến độ và quản lý nội dung CMS (PUB-01→05 - BE3)."""

from typing import Literal

from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import Field

from . import public_site
from .api_common import Body, Limit, NameText, NoteText, Offset, get_conn, idempotent_write, now_of, role_dependency
from .auth import CMS_WRITE_ROLES, Principal
from .db import write_tx
from .errors import DomainError

router = APIRouter()
cms_editor = role_dependency(CMS_WRITE_ROLES)


def _client_ip(request: Request) -> str:
    forwarded = (request.headers.get("X-Forwarded-For") or "").strip()
    if forwarded:
        return forwarded.split(",")[0].strip() or "127.0.0.1"
    if request.client and request.client.host:
        return request.client.host
    return "127.0.0.1"


def _forbid_token_in_url(request: Request) -> None:
    for param in request.query_params.keys():
        if "token" in param.lower():
            raise DomainError(
                "Không được truyền token tra cứu trên URL. Vui lòng gửi trong thân POST request.",
                code="TOKEN_IN_URL_FORBIDDEN",
                details={"field": param},
            )


# ---------- PUB-01: Xem nội dung website công khai ----------

@router.get("/public/contents")
def list_public_contents(
    request: Request,
    content_type: Literal["COMPANY", "SERVICE", "FAQ", "CONTACT"] | None = None,
    service_type: Literal["TRANSPORT", "RENTAL", "WAREHOUSE"] | None = None,
    conn=Depends(get_conn),
):
    _forbid_token_in_url(request)
    return public_site.list_published_contents(
        conn, content_type=content_type, service_type=service_type
    )


@router.get("/public/contents/{slug}")
def get_public_content(slug: str, request: Request, conn=Depends(get_conn)):
    _forbid_token_in_url(request)
    return public_site.get_published_content_by_slug(conn, slug)


@router.get("/public/catalogs")
def get_public_catalogs(conn=Depends(get_conn)):
    return public_site.get_public_catalogs(conn)


# ---------- PUB-02 & PUB-03: Gửi yêu cầu dịch vụ & nhận mã + token ----------

class PublicRequestIn(Body):
    service_type: Literal["TRANSPORT", "RENTAL", "WAREHOUSE"]
    contact: dict
    service_data: dict


@router.post("/public/requests", status_code=201)
def submit_public_request(
    body: PublicRequestIn,
    request: Request,
    response: Response,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    conn=Depends(get_conn),
):
    _forbid_token_in_url(request)
    client_ip = _client_ip(request)
    now = now_of(request)
    anon_principal = Principal(username=f"public:{client_ip}", roles=frozenset())
    return idempotent_write(
        conn,
        response,
        key=idempotency_key,
        principal=anon_principal,
        operation="public.request.submit",
        body=body,
        now=now,
        status_code=201,
        action=lambda: public_site.submit_public_request(
            conn,
            client_ip=client_ip,
            service_type=body.service_type,
            contact=body.contact,
            service_data=body.service_data,
            now=now,
        ),
    )


# ---------- PUB-04: Tra cứu tiến độ công khai (chỉ POST, không nhận token trên URL) ----------

class PublicLookupIn(Body):
    request_code: str = Field(min_length=1, max_length=50)
    token: str = Field(min_length=1, max_length=200)


@router.post("/public/lookup")
def lookup_public_progress(
    body: PublicLookupIn,
    request: Request,
    conn=Depends(get_conn),
):
    _forbid_token_in_url(request)
    return public_site.lookup_public_progress(
        conn,
        client_ip=_client_ip(request),
        request_code=body.request_code,
        token=body.token,
        now=now_of(request),
    )


@router.get("/public/lookup")
def reject_get_lookup():
    raise DomainError(
        "Tra cứu tiến độ bắt buộc dùng POST /api/public/lookup để không lộ token trên URL.",
        code="TOKEN_IN_URL_FORBIDDEN",
    )


# ---------- PUB-05: Quản lý nội dung công khai (CMS) ----------

class CmsContentCreateIn(Body):
    slug: str = Field(min_length=2, max_length=100)
    content_type: Literal["COMPANY", "SERVICE", "FAQ", "CONTACT"]
    service_type: Literal["TRANSPORT", "RENTAL", "WAREHOUSE"] | None = None
    title: NameText
    summary: NoteText = ""
    body_html: str = Field(min_length=1, max_length=50_000)
    meta: dict | None = None
    sort_order: int = 0
    status: Literal["DRAFT", "PUBLISHED", "HIDDEN"] = "DRAFT"


class CmsContentUpdateIn(Body):
    version: int = Field(ge=1)
    title: str | None = Field(None, min_length=1, max_length=200)
    summary: str | None = Field(None, max_length=2000)
    body_html: str | None = Field(None, min_length=1, max_length=50_000)
    meta: dict | None = None
    sort_order: int | None = None
    reason: str | None = Field(None, max_length=2000)


class CmsContentStatusIn(Body):
    version: int = Field(ge=1)
    status: Literal["DRAFT", "PUBLISHED", "HIDDEN"]
    reason: str | None = Field(None, max_length=2000)


@router.get("/admin/cms/contents")
def list_cms_contents(
    status: Literal["DRAFT", "PUBLISHED", "HIDDEN"] | None = None,
    content_type: Literal["COMPANY", "SERVICE", "FAQ", "CONTACT"] | None = None,
    service_type: Literal["TRANSPORT", "RENTAL", "WAREHOUSE"] | None = None,
    q: str | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
    _: Principal = Depends(cms_editor),
    conn=Depends(get_conn),
):
    return public_site.list_cms_contents(
        conn,
        status=status,
        content_type=content_type,
        service_type=service_type,
        q=q,
        limit=limit,
        offset=offset,
    )


@router.get("/admin/cms/contents/{code}")
def get_cms_content(
    code: str,
    _: Principal = Depends(cms_editor),
    conn=Depends(get_conn),
):
    return public_site.get_cms_content(conn, code)


@router.post("/admin/cms/contents", status_code=201)
def create_cms_content(
    body: CmsContentCreateIn,
    request: Request,
    p: Principal = Depends(cms_editor),
    conn=Depends(get_conn),
):
    with write_tx(conn):
        return public_site.create_cms_content(
            conn, **body.model_dump(), actor=p.username, now=now_of(request)
        )


@router.patch("/admin/cms/contents/{code}")
def update_cms_content(
    code: str,
    body: CmsContentUpdateIn,
    request: Request,
    p: Principal = Depends(cms_editor),
    conn=Depends(get_conn),
):
    with write_tx(conn):
        return public_site.update_cms_content(
            conn,
            code,
            expected_version=body.version,
            title=body.title,
            summary=body.summary,
            body_html=body.body_html,
            meta=body.meta,
            sort_order=body.sort_order,
            reason=body.reason,
            actor=p.username,
            now=now_of(request),
        )


@router.post("/admin/cms/contents/{code}/status")
def set_cms_content_status(
    code: str,
    body: CmsContentStatusIn,
    request: Request,
    p: Principal = Depends(cms_editor),
    conn=Depends(get_conn),
):
    with write_tx(conn):
        return public_site.set_cms_content_status(
            conn,
            code,
            expected_version=body.version,
            status=body.status,
            reason=body.reason,
            actor=p.username,
            now=now_of(request),
        )
