"""execution 上下文的 HTTP 接口。"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Query, Response

from agent_platform.contexts.execution.api.schemas import (
    InvokeRequest,
    InvokeResponse,
    PreviewRequest,
    PreviewResponse,
    SessionItemResponse,
    SessionPageResponse,
    UsageResponse,
)
from agent_platform.contexts.execution.application.invocation_service import invocation_service
from agent_platform.contexts.execution.application.preview_service import preview_service
from agent_platform.contexts.execution.domain.enums import RunKind
from agent_platform.contexts.execution.infrastructure.repositories import ExecutionSessionRepository
from agent_platform.contexts.platform.api.dependencies import CurrentActor
from agent_platform.contexts.platform.application.authorization_service import AuthorizationService
from agent_platform.contexts.platform.domain.permissions import Permission
from agent_platform.shared.db import session_scope

router = APIRouter()
authorization = AuthorizationService()


@router.post(
    "/agents/{agent_id}/invoke",
    response_model=InvokeResponse,
    tags=["runs"],
)
async def invoke_agent(
    agent_id: uuid.UUID,
    payload: InvokeRequest,
    actor: CurrentActor,
    response: Response,
) -> InvokeResponse:
    await authorization.require_permission(actor, Permission.AGENT_INVOKE)
    result = await invocation_service.invoke(
        actor=actor,
        agent_id=agent_id,
        env=payload.env,
        values=payload.input,
        session_id=payload.session_id,
        timeout_s=payload.timeout_s,
    )
    response.headers["X-Trace-Id"] = result.trace_id
    return InvokeResponse(
        run_id=result.run_id,
        trace_id=result.trace_id,
        status=result.status,
        output=result.output,
        usage=UsageResponse(steps=result.usage["steps"], tokens=result.usage["tokens"]),
        error=result.error,
    )


@router.post(
    "/agents/{agent_id}/preview",
    response_model=PreviewResponse,
    tags=["runs"],
    description=(
        "调试预览：按提交的引用即时装配并执行，返回 run id、输出、`config_hash` 与错误，"
        "不产生版本、不改发布指针。与保存版本共用同一装配管线，同一组引用会得到相同 `config_hash`。"
        "归档 Agent 返回 409 `AGENT_ARCHIVED`；引用不可用返回 409 `RESOURCE_UNAVAILABLE`。"
    ),
)
async def preview_agent(
    agent_id: uuid.UUID,
    payload: PreviewRequest,
    actor: CurrentActor,
    response: Response,
) -> PreviewResponse:
    await authorization.require_permission(actor, Permission.AGENT_INVOKE)
    result = await preview_service.preview(
        actor=actor,
        agent_id=agent_id,
        env=payload.env,
        model_id=payload.model_id,
        prompt_version_id=payload.prompt_version_id,
        tool_version_ids=payload.tool_version_ids,
        params=payload.params,
        policy=payload.policy,
        values=payload.input,
        preview_session_id=payload.preview_session_id,
        timeout_s=payload.timeout_s,
    )
    response.headers["X-Trace-Id"] = result.trace_id
    return PreviewResponse(
        run_id=result.run_id,
        trace_id=result.trace_id,
        status=result.status,
        output=result.output,
        usage=UsageResponse(steps=result.usage["steps"], tokens=result.usage["tokens"]),
        error=result.error,
        config_hash=result.config_hash,
        preview_snapshot=result.preview_snapshot,
    )


@router.get("/sessions", response_model=SessionPageResponse, tags=["runs"])
async def list_sessions(
    actor: CurrentActor,
    agent_id: uuid.UUID,
    kind: RunKind | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> SessionPageResponse:
    """按 Agent 与活跃时间范围列出会话及其绑定版本（只含当前租户）。"""
    await authorization.require_permission(actor, Permission.RUN_READ)
    async with session_scope(actor.tenant_id) as session:
        rows, total = await ExecutionSessionRepository().list_for_agent(
            session,
            tenant_id=actor.tenant_id,
            agent_id=agent_id,
            kind=kind,
            start=start,
            end=end,
            page=page,
            page_size=page_size,
        )
    return SessionPageResponse(
        items=[
            SessionItemResponse(
                id=row.id,
                agent_id=row.agent_id,
                env=row.env,
                kind=row.kind,
                session_key=row.session_key,
                agent_release_id=row.agent_release_id,
                agent_version_id=row.agent_version_id,
                version_no=version_no,
                status=row.status,
                last_active_at=row.last_active_at,
                expires_at=row.expires_at,
                created_at=row.created_at,
            )
            for row, version_no in rows
        ],
        total=total,
        page=page,
        page_size=page_size,
    )
