"""Quy ước thời gian: lưu UTC dạng chuỗi cố định, hiển thị theo Asia/Ho_Chi_Minh."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

SYSTEM_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
DB_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
# Thời điểm dự kiến mới phải ở hiện tại/tương lai (SRS mục 5). Cho trễ 1 phút
# để người dùng chọn "bây giờ" trên form không bị từ chối vì độ trễ mạng.
PLANNED_START_GRACE = timedelta(minutes=1)


def to_utc(value: datetime) -> datetime:
    """Giờ không kèm múi giờ được hiểu theo giờ hệ thống Asia/Ho_Chi_Minh."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=SYSTEM_TZ)
    return value.astimezone(timezone.utc).replace(microsecond=0)


def to_db(value: datetime) -> str:
    return to_utc(value).strftime(DB_FORMAT)


def from_db(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.strptime(value, DB_FORMAT).replace(tzinfo=timezone.utc)


def system_now() -> datetime:
    return datetime.now(timezone.utc)
