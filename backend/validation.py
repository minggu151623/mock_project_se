"""Kiểm tra đầu vào dùng chung theo SRS mục 5."""

from .errors import Conflict, ValidationFailed

NAME_MAX = 200
NOTE_MAX = 2000


def clean_text(value: str | None, field: str, *, max_len: int, required: bool = True) -> str:
    """Bỏ khoảng trắng đầu/cuối; lỗi chỉ rõ trường cần sửa."""
    text = (value or "").strip()
    if required and not text:
        raise ValidationFailed(f"Trường {field} là bắt buộc.", details={"field": field})
    if len(text) > max_len:
        raise ValidationFailed(f"Trường {field} tối đa {max_len} ký tự.", details={"field": field})
    return text


def check_version(current: int, expected: int) -> None:
    """Hai người cùng sửa một hồ sơ: người lưu sau được báo, không ghi đè âm thầm."""
    if current != expected:
        raise Conflict(
            "Hồ sơ đã được người khác cập nhật. Tải lại để xem thay đổi mới nhất.",
            code="VERSION_CONFLICT",
            details={"current_version": current, "your_version": expected},
        )
