"""v1 路由聚合。每个上下文一个 router，统一挂载在 /api/v1 下。"""

from __future__ import annotations

from fastapi import APIRouter

from agent_platform.contexts.agent.api.routes import router as agent_router
from agent_platform.contexts.catalog.api.routes import router as catalog_router
from agent_platform.contexts.execution.api.routes import router as execution_router
from agent_platform.contexts.insight.api.routes import router as insight_router
from agent_platform.contexts.platform.api.routes import router as platform_router

api_router = APIRouter()
api_router.include_router(platform_router)
api_router.include_router(catalog_router)
api_router.include_router(agent_router)
api_router.include_router(execution_router)
api_router.include_router(insight_router)
