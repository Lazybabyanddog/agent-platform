"""异常到统一响应体的映射。"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from agent_platform.shared.context import get_trace_id
from agent_platform.shared.errors import AppError
from agent_platform.shared.logging import get_logger

log = get_logger(__name__)


def error_body(
    code: str,
    message: str,
    *,
    details: list[dict[str, Any]] | None = None,
    trace_id: str | None = None,
) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details or [],
            "trace_id": trace_id or get_trace_id(),
        }
    }


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        log.warning(
            "app_error",
            code=exc.code,
            status=exc.http_status,
            path=request.url.path,
            message=exc.message,
        )
        return JSONResponse(
            status_code=exc.http_status,
            content=error_body(exc.code, exc.message, details=exc.details),
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_request_validation(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        details = [
            {
                "field": ".".join(str(part) for part in err.get("loc", ()) if part != "body"),
                "reason": err.get("msg", "invalid value"),
                "type": err.get("type", "value_error"),
            }
            for err in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content=error_body("VALIDATION_FAILED", "request validation failed", details=details),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http_exception(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        code = {
            401: "UNAUTHORIZED",
            403: "FORBIDDEN",
            404: "NOT_FOUND",
            405: "METHOD_NOT_ALLOWED",
            429: "RATE_LIMITED",
        }.get(exc.status_code, "HTTP_ERROR")
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(code, str(exc.detail)),
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled_exception", path=request.url.path, error=repr(exc))
        return JSONResponse(
            status_code=500,
            content=error_body("INTERNAL_ERROR", "internal error"),
        )
