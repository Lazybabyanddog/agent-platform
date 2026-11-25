"""FastAPI 应用工厂。"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from agent_platform.api.v1.router import api_router
from agent_platform.settings import Settings, get_settings
from agent_platform.shared.eventloop import ensure_psycopg_compatible_loop
from agent_platform.shared.exception_handlers import register_exception_handlers
from agent_platform.shared.logging import configure_logging
from agent_platform.shared.middleware import TraceMiddleware

ensure_psycopg_compatible_loop()


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings, service="api")

    app = FastAPI(
        title="Agent Platform API",
        version="0.1.0",
        description="企业 Agent 中台 - 一期（同步执行闭环）",
        debug=settings.debug,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.add_middleware(TraceMiddleware)
    register_exception_handlers(app)
    app.include_router(api_router, prefix=settings.api_prefix)

    @app.get("/healthz", tags=["ops"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "service": settings.app_name, "env": settings.environment}

    return app


app = create_app()
