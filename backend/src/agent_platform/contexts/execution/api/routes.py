"""execution 上下文的 HTTP 接口。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Query, Response

from agent_platform.contexts.agent.domain.enums import Environment
from agent_platform.contexts.execution.api.schemas import (
    InvokeRequest,
    InvokeResponse,
    PreviewRequest,
    PreviewResponse,
    SessionHistoryMessageResponse,
    SessionHistoryResponse,
    SessionHistoryToolResponse,
    SessionHistoryTurnResponse,
    SessionItemResponse,
    SessionPageResponse,
    UsageResponse,
)
from agent_platform.contexts.execution.application.invocation_service import invocation_service
from agent_platform.contexts.execution.application.preview_service import preview_service
from agent_platform.contexts.execution.application.session_history_service import (
    SessionHistoryService,
    session_expired,
)
from agent_platform.contexts.execution.domain.enums import RunKind
from agent_platform.contexts.execution.infrastructure.repositories import ExecutionSessionRepository
from agent_platform.contexts.execution.infrastructure.tables import ExecutionSession
from agent_platform.contexts.platform.api.dependencies import CurrentActor
from agent_platform.contexts.platform.application.authorization_service import AuthorizationService
from agent_platform.contexts.platform.domain.permissions import Permission
from agent_platform.shared.context import ActorContext
from agent_platform.shared.db import session_scope
from agent_platform.shared.hashing import config_hash as compute_config_hash

router = APIRouter()
authorization = AuthorizationService()
session_history = SessionHistoryService()


def _session_item(
    row: ExecutionSession,
    *,
    actor: ActorContext,
    version_no: int | None = None,
    now: datetime | None = None,
) -> SessionItemResponse:
    snapshot: dict[str, Any] = dict(row.preview_snapshot or {})
    return SessionItemResponse(
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
        title=row.title,
        turn_count=row.turn_count,
        owned=row.created_by is None or row.created_by == actor.actor_id,
        expired=session_expired(row, now),
        preview_config_hash=compute_config_hash(snapshot) if snapshot else None,
    )


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
        runtime_context=payload.runtime_context,
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


@router.get(
    "/sessions",
    response_model=SessionPageResponse,
    tags=["runs"],
    description=(
        "按 Agent 列出会话及其绑定版本（只含当前租户），按最近活动倒序分页。"
        "默认只返回本人创建的会话（`mine_only=true`），未记录创建者的存量会话在同租户内可见；"
        "过期会话默认仍返回并标记 `expired=true`。"
    ),
)
async def list_sessions(
    actor: CurrentActor,
    agent_id: uuid.UUID,
    kind: RunKind | None = None,
    env: Environment | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    include_expired: bool = True,
    mine_only: bool = True,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> SessionPageResponse:
    await authorization.require_permission(actor, Permission.RUN_READ)
    async with session_scope(actor.tenant_id) as session:
        rows, total = await ExecutionSessionRepository().list_for_agent(
            session,
            tenant_id=actor.tenant_id,
            agent_id=agent_id,
            kind=kind,
            env=env,
            start=start,
            end=end,
            owner_id=actor.actor_id if mine_only else None,
            include_expired=include_expired,
            page=page,
            page_size=page_size,
        )
    now = datetime.now(UTC)
    return SessionPageResponse(
        items=[
            _session_item(row, actor=actor, version_no=version_no, now=now)
            for row, version_no in rows
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/sessions/{session_id}/history",
    response_model=SessionHistoryResponse,
    tags=["runs"],
    description=(
        "只读回放：从会话线程读取结构化轮次（用户输入、回答、该轮工具条目与运行元数据），"
        "不产生运行、不改动会话绑定。正文留存关闭时只返回长度与工具摘要；"
        "他人会话与不存在会话统一 404。"
    ),
)
async def get_session_history(
    session_id: uuid.UUID,
    actor: CurrentActor,
    turns_limit: int | None = Query(default=None, ge=1, le=200),
) -> SessionHistoryResponse:
    await authorization.require_permission(actor, Permission.AGENT_READ)
    view = await session_history.get(
        actor=actor, session_id=session_id, turns_limit=turns_limit
    )
    return SessionHistoryResponse(
        session=_session_item(view.session, actor=actor),
        turns=[
            SessionHistoryTurnResponse(
                index=turn.index,
                user=SessionHistoryMessageResponse(
                    content=turn.user.content, length=turn.user.length
                ),
                assistant=(
                    SessionHistoryMessageResponse(
                        content=turn.assistant.content, length=turn.assistant.length
                    )
                    if turn.assistant is not None
                    else None
                ),
                tools=[
                    SessionHistoryToolResponse(
                        name=tool.name,
                        status=tool.status,
                        latency_ms=tool.latency_ms,
                        error=tool.error,
                    )
                    for tool in turn.tools
                ],
                run_id=turn.run_id,
                trace_id=turn.trace_id,
                status=turn.status,
                error=turn.error,
                started_at=turn.started_at,
            )
            for turn in view.turns
        ],
        truncated=view.truncated,
        reconstructed=view.reconstructed,
    )
