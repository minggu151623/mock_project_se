"""Điểm nối với phần của BE1 (đơn hàng và phụ phí).

BE2 không sở hữu ServiceOrder hay phụ phí, nhưng cần:
- hỏi số phụ phí PENDING trước khi hoàn tất chuyến/thuê/kho (TRN-04, RNT-05, WHS-06);
- báo đơn đã bắt đầu thực hiện (đơn sang IN_PROGRESS) và đã hoàn tất (COMPLETED).

Mặc định: không có phụ phí chờ, báo đơn thì không làm gì. BE1 gán hàm thật lúc khởi động:

    from backend import integrations
    integrations.hooks.pending_charge_count = my_count
    integrations.hooks.operation_started = my_started
    integrations.hooks.operation_completed = my_completed

Các hàm nhận cùng kết nối và chạy trong cùng transaction với thao tác của BE2,
nên đơn và bản ghi vận hành luôn đổi trạng thái cùng nhau.
"""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

# (conn, order_code) -> số phụ phí PENDING
PendingChargeCount = Callable[[sqlite3.Connection, str], int]
# (conn, order_code, service_type, at) -> None. service_type: TRANSPORT | RENTAL | WAREHOUSE
OperationEvent = Callable[[sqlite3.Connection, str, str, datetime], None]


def _no_pending(conn: sqlite3.Connection, order_code: str) -> int:
    return 0


def _ignore(conn: sqlite3.Connection, order_code: str, service_type: str, at: datetime) -> None:
    return None


@dataclass
class OrderHooks:
    pending_charge_count: PendingChargeCount = _no_pending
    operation_started: OperationEvent = _ignore
    operation_completed: OperationEvent = _ignore


hooks = OrderHooks()


def reset() -> None:
    """Đưa về mặc định. Dùng trong test."""
    hooks.pending_charge_count = _no_pending
    hooks.operation_started = _ignore
    hooks.operation_completed = _ignore
