"""统一错误模型。

所有对外错误都是 AppError 的子类，响应体固定为
{"error": {"code", "message", "details", "trace_id"}}。
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    code = "INTERNAL_ERROR"
    http_status = 500
    message = "internal error"

    def __init__(
        self,
        message: str | None = None,
        *,
        details: list[dict[str, Any]] | None = None,
        code: str | None = None,
        http_status: int | None = None,
    ) -> None:
        self.message = message or self.message
        self.details = details or []
        if code is not None:
            self.code = code
        if http_status is not None:
            self.http_status = http_status
        super().__init__(self.message)


class ValidationFailedError(AppError):
    code = "VALIDATION_FAILED"
    http_status = 422
    message = "request validation failed"

    @classmethod
    def for_field(cls, field: str, reason: str) -> ValidationFailedError:
        return cls(details=[{"field": field, "reason": reason}])


class UnauthorizedError(AppError):
    code = "UNAUTHORIZED"
    http_status = 401
    message = "authentication required"


class ForbiddenError(AppError):
    code = "FORBIDDEN"
    http_status = 403
    message = "permission denied"


class NotFoundError(AppError):
    code = "NOT_FOUND"
    http_status = 404
    message = "resource not found"


class ConflictError(AppError):
    code = "CONFLICT"
    http_status = 409
    message = "resource conflict"


class RateLimitedError(AppError):
    code = "RATE_LIMITED"
    http_status = 429
    message = "rate limit exceeded"


class UnavailableError(AppError):
    code = "UNAVAILABLE"
    http_status = 503
    message = "dependency unavailable"


class SyncTimeoutExceededError(AppError):
    """同步接口无法受理该执行时长，需改用异步接口。"""

    code = "SYNC_TIMEOUT_EXCEEDED"
    http_status = 400
    message = "requested timeout exceeds the synchronous invocation limit; use the async invocation API"


class ResourceUnavailableError(AppError):
    code = "RESOURCE_UNAVAILABLE"
    http_status = 409
    message = "referenced resource is unavailable"


class ModelRateLimitedError(RateLimitedError):
    def __init__(self, message: str = "model rate limit exceeded", **kwargs: Any) -> None:
        super().__init__(message, code="MODEL_RATE_LIMITED", **kwargs)

