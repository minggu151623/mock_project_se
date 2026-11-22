"""Xác định người gọi và kiểm tra vai trò.

Đăng nhập và phiên thật thuộc BE3 (ADM-01/02). Ở đây chỉ có điểm nối:
app.state.principal_provider(request) -> Principal. Mặc định từ chối mọi request.
Khi chạy dev với DEV_AUTH=1, đọc header X-Dev-User và X-Dev-Roles. Không bật trên
môi trường thật.
"""

from collections.abc import Callable
from dataclasses import dataclass

from fastapi import Request

from .errors import Forbidden, Unauthorized

# Vận tải, thuê xe: DSP thao tác; SAL/MGR xem (TRN, RNT).
READ_ROLES = frozenset({"DSP", "SAL", "MGR"})
WRITE_ROLES = frozenset({"DSP"})
# Kho: WHS thao tác; MGR duyệt điều chỉnh; SAL/DSP xem phần được cấp (WHS).
WAREHOUSE_READ_ROLES = frozenset({"WHS", "MGR", "SAL", "DSP"})
WAREHOUSE_WRITE_ROLES = frozenset({"WHS"})
APPROVER_ROLES = frozenset({"MGR"})
# Tệp bằng chứng: người lập biên bản tải lên; người xem nghiệp vụ tải về (NFR-07).
ATTACHMENT_WRITE_ROLES = frozenset({"DSP", "WHS"})
ATTACHMENT_READ_ROLES = frozenset({"DSP", "WHS", "SAL", "MGR"})


@dataclass(frozen=True)
class Principal:
    username: str
    roles: frozenset[str]


PrincipalProvider = Callable[[Request], Principal]


def deny_all_provider(request: Request) -> Principal:
    raise Unauthorized("Cần đăng nhập.")


def dev_header_provider(request: Request) -> Principal:
    user = (request.headers.get("X-Dev-User") or "").strip()
    if not user:
        raise Unauthorized("Cần đăng nhập.")
    roles = frozenset(
        r.strip().upper() for r in (request.headers.get("X-Dev-Roles") or "").split(",") if r.strip()
    )
    return Principal(username=user, roles=roles)


def current_principal(request: Request) -> Principal:
    return request.app.state.principal_provider(request)


def require_roles(principal: Principal, allowed: frozenset[str]) -> Principal:
    if not principal.roles & allowed:
        raise Forbidden("Tài khoản không có quyền thực hiện thao tác này.")
    return principal
