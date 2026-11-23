"""请求级中间件：trace_id 注入与传播。"""

from __future__ import annotations

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from agent_platform.shared.context import set_actor, set_request_id, set_trace_id
from agent_platform.shared.ids import new_id_str

TRACE_HEADER = "X-Trace-Id"
REQUEST_HEADER = "X-Request-Id"


def _inbound_trace_id(request: Request) -> str | None:
    value = request.headers.get(TRACE_HEADER) or request.headers.get("traceparent")
    if not value:
        return None
    candidate = value.strip()
    if len(candidate) > 128 or not candidate.replace("-", "").isalnum():
        return None
    return candidate


class TraceMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next) -> Response:
        trace_id = _inbound_trace_id(request) or new_id_str()
        request_id = request.headers.get(REQUEST_HEADER) or new_id_str()

        set_trace_id(trace_id)
        set_request_id(request_id)
        # 每个请求从"未认证"开始，避免上一次请求的调用者泄漏到本次
        set_actor(None)
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(trace_id=trace_id, request_id=request_id)

        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.clear_contextvars()

        response.headers[TRACE_HEADER] = trace_id
        response.headers[REQUEST_HEADER] = request_id
        return response
