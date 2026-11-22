"""Phần dùng chung cho các router: kết nối, vai trò, đồng hồ, chống gửi trùng."""

from collections.abc import Callable
from datetime import datetime
from typing import Annotated

from fastapi import Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from .auth import Principal, current_principal, require_roles
from .db import connect, write_tx
from .idempotency import run_idempotent


def get_conn(request: Request):
    conn = connect(request.app.state.db_path)
    try:
        yield conn
    finally:
        conn.close()


def role_dependency(allowed: frozenset[str]) -> Callable[[Request], Principal]:
    def dependency(request: Request) -> Principal:
        return require_roles(current_principal(request), allowed)
    return dependency


def now_of(request: Request) -> datetime:
    return request.app.state.clock()


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


ReasonText = Annotated[str, Field(min_length=1, max_length=2000)]
NoteText = Annotated[str, Field(max_length=2000)]
NameText = Annotated[str, Field(min_length=1, max_length=200)]
Limit = Annotated[int, Query(ge=1, le=200)]
Offset = Annotated[int, Query(ge=0)]


def idempotent_write(
    conn, response: Response, *, key: str | None, principal: Principal, operation: str, body: BaseModel,
    now: datetime, action: Callable[[], dict], status_code: int = 200,
) -> dict:
    """Ghi trong một transaction. Gửi lại cùng Idempotency-Key trả đúng kết quả lần đầu (NFR-05)."""
    with write_tx(conn):
        status, payload = run_idempotent(
            conn, key=key, actor=principal.username, operation=operation, payload=body.model_dump(mode="json"),
            now=now, action=lambda: (status_code, action()))
    response.status_code = status
    return payload
