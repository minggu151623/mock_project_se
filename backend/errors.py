"""Lỗi nghiệp vụ có mã ổn định để FE hiển thị và test kiểm tra."""


class DomainError(Exception):
    status_code = 400
    default_code = "DOMAIN_ERROR"

    def __init__(self, message: str, *, code: str | None = None, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.code = code or self.default_code
        self.details = details or {}


class NotFound(DomainError):
    status_code = 404
    default_code = "NOT_FOUND"


class ValidationFailed(DomainError):
    status_code = 422
    default_code = "VALIDATION_ERROR"


class Conflict(DomainError):
    status_code = 409
    default_code = "CONFLICT"


class Unauthorized(DomainError):
    status_code = 401
    default_code = "UNAUTHORIZED"


class Forbidden(DomainError):
    status_code = 403
    default_code = "FORBIDDEN"


class BookingConflict(Conflict):
    """Lịch mới chồng lên lịch khác. details['conflicts'] liệt kê các lịch đó."""

    default_code = "BOOKING_CONFLICT"
