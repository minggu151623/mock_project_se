"""Tạo ứng dụng FastAPI.

Chạy dev:  DEV_AUTH=1 .venv/bin/uvicorn backend.main:create_app --factory --reload
"""

import logging
import os
import uuid
from collections.abc import Callable
from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from . import api, api_operations, api_warehouse, api_sales, sales
from .auth import PrincipalProvider, deny_all_provider, dev_header_provider
from .db import connect, init_db
from .errors import DomainError
from .timeutil import system_now

log = logging.getLogger("backend")


def _error(
    status: int, code: str, message: str, details: dict | None = None
) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "details": details or {}}},
    )


def create_app(
    db_path: str | None = None,
    *,
    files_dir: str | None = None,
    principal_provider: PrincipalProvider | None = None,
    clock: Callable[[], datetime] | None = None,
) -> FastAPI:
    sales.bind_integrations()
    db_path = db_path or os.environ.get("BE_DB_PATH", "data/dev.sqlite3")
    conn = connect(db_path)
    try:
        init_db(conn)
    finally:
        conn.close()

    app = FastAPI(title="Mock Project – Backend", version="0.3.0")
    app.state.db_path = db_path
    app.state.files_dir = files_dir or os.environ.get("BE_FILES_DIR", "data/files")
    if principal_provider is None:
        principal_provider = (
            dev_header_provider
            if os.environ.get("DEV_AUTH") == "1"
            else deny_all_provider
        )
    app.state.principal_provider = principal_provider
    app.state.clock = clock or system_now

    @app.middleware("http")
    async def request_id(request: Request, call_next):
        # NFR-08: mỗi lần xử lý có mã để tra log.
        request.state.request_id = uuid.uuid4().hex[:16]
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.exception_handler(DomainError)
    async def domain_error(request: Request, exc: DomainError):
        return _error(exc.status_code, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        fields = [
            {"field": ".".join(str(p) for p in e["loc"][1:]), "message": e["msg"]}
            for e in exc.errors()
        ]
        return _error(
            422,
            "VALIDATION_ERROR",
            "Dữ liệu không hợp lệ. Kiểm tra các trường được nêu.",
            {"fields": fields},
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception):
        # NFR-10: không trả stack trace hay chi tiết nội bộ cho người dùng.
        rid = getattr(request.state, "request_id", "-")
        log.exception("Unhandled error request_id=%s", rid)
        return _error(500, "INTERNAL_ERROR", f"Lỗi hệ thống. Mã tra cứu: {rid}.")

    app.include_router(api.router, prefix="/api")
    app.include_router(api_operations.router, prefix="/api")
    app.include_router(api_warehouse.router, prefix="/api")
    app.include_router(api_sales.router, prefix="/api")
    return app
