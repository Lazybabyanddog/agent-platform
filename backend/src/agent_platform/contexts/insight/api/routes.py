"""insight API：运行轨迹与指标。"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Query

from agent_platform.contexts.execution.domain.enums import RunKind
from agent_platform.contexts.insight.api.schemas import (
    RunMetricsResponse,
    RunPageResponse,
    RunSummaryItemResponse,
    RunTraceResponse,
    ToolCallErrorCount,
    ToolCallItemResponse,
    ToolCallOverviewResponse,
    TraceContent,
    TraceEntryResponse,
    TraceRunResponse,
    TraceTokenUsage,
)
from agent_platform.contexts.insight.application.metrics_service import MetricsService
from agent_platform.contexts.insight.application.tool_call_service import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    tool_call_service,
)
from agent_platform.contexts.insight.application.trace_service import TraceService
from agent_platform.contexts.platform.api.dependencies import CurrentActor
from agent_platform.contexts.platform.application.authorization_service import (
    AuthorizationService,
)
from agent_platform.contexts.platform.domain.permissions import Permission

router = APIRouter()
authorization = AuthorizationService()
trace_service = TraceService()
metrics_service = MetricsService()


@router.get("/runs", response_model=RunPageResponse, tags=["insight"])
async def list_runs(
    actor: CurrentActor,
    agent_id: uuid.UUID | None = None,
    kind: RunKind | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> RunPageResponse:
    await authorization.require_permission(actor, Permission.RUN_READ)
    rows, total = await trace_service.list_runs(
        tenant_id=actor.tenant_id,
        agent_id=agent_id,
        kind=kind,
        start=start,
        end=end,
        page=page,
        page_size=page_size,
    )
    return RunPageResponse(
        items=[
            RunSummaryItemResponse(
                run_id=run.id,
                trace_id=run.trace_id,
                agent_id=run.agent_id,
                env=str(run.env),
                kind=str(run.kind),
                status=str(run.status),
                config_hash=run.config_hash,
                agent_release_id=run.agent_release_id,
                agent_version_id=run.agent_version_id,
                execution_session_id=run.execution_session_id,
                session_id=run.session_id,
                started_at=run.started_at,
                finished_at=run.finished_at,
            )
            for run in rows
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/runs/{run_id}/trace", response_model=RunTraceResponse, tags=["insight"])
async def get_run_trace(run_id: uuid.UUID, actor: CurrentActor) -> RunTraceResponse:
    await authorization.require_permission(actor, Permission.RUN_READ)
    include_content = actor.is_admin or actor.has_permission(
        Permission.INSIGHT_READ_CONTENT.value
    )
    view = await trace_service.get(
        tenant_id=actor.tenant_id, run_id=run_id, actor_can_read_content=include_content
    )
    run = view.run
    return RunTraceResponse(
        run=TraceRunResponse(
            run_id=run.id,
            trace_id=run.trace_id,
            agent_id=run.agent_id,
            env=str(run.env),
            kind=str(run.kind),
            config_hash=run.config_hash,
            agent_release_id=run.agent_release_id,
            agent_version_id=run.agent_version_id,
            execution_session_id=run.execution_session_id,
            session_id=run.session_id,
            status=str(run.status),
            caller_type=str(run.caller_type),
            started_at=run.started_at,
            finished_at=run.finished_at,
            error=run.error,
        ),
        entries=[
            TraceEntryResponse(
                type=entry.type,
                trace_id=entry.trace_id,
                span_id=entry.span_id,
                label=entry.label,
                status=entry.status,
                error_code=entry.error_code,
                started_at=entry.started_at,
                ended_at=entry.ended_at,
                latency_ms=entry.latency_ms,
                summary=entry.summary,
                tokens=TraceTokenUsage(**entry.tokens) if entry.tokens else None,
                content=TraceContent(**entry.content) if entry.content else None,
                is_slowest=entry.span_id == view.slowest_span_id,
            )
            for entry in view.entries
        ],
        slowest_span_id=view.slowest_span_id,
        slowest_latency_ms=view.slowest_latency_ms,
    )


@router.get("/tool-calls", response_model=ToolCallOverviewResponse, tags=["tools"])
async def list_tool_calls(
    actor: CurrentActor,
    tool_id: uuid.UUID,
    tool_version_id: uuid.UUID | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
) -> ToolCallOverviewResponse:
    await authorization.require_permission(actor, Permission.TOOL_READ)
    overview = await tool_call_service.overview(
        tenant_id=actor.tenant_id,
        tool_id=tool_id,
        tool_version_id=tool_version_id,
        start=start,
        end=end,
        limit=limit,
    )
    return ToolCallOverviewResponse(
        tool_id=overview.tool_id,
        window_start=overview.window_start,
        window_end=overview.window_end,
        total=overview.total,
        succeeded=overview.succeeded,
        failed=overview.failed,
        success_rate=overview.success_rate,
        avg_latency_ms=overview.avg_latency_ms,
        errors=[ToolCallErrorCount(code=code, count=count) for code, count in overview.errors],
        items=[ToolCallItemResponse.model_validate(item) for item in overview.items],
    )


@router.get("/metrics/runs", response_model=RunMetricsResponse, tags=["metrics"])
async def get_run_metrics(
    actor: CurrentActor,
    agent_id: uuid.UUID | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    kind: RunKind | None = None,
    include_preview: bool = False,
) -> RunMetricsResponse:
    await authorization.require_permission(actor, Permission.RUN_READ)
    metrics = await metrics_service.run_metrics(
        tenant_id=actor.tenant_id,
        agent_id=agent_id,
        start=start,
        end=end,
        kind=kind,
        include_preview=include_preview,
    )
    return RunMetricsResponse(
        agent_id=metrics.agent_id,
        window_start=metrics.window_start,
        window_end=metrics.window_end,
        kind=str(kind) if kind is not None else None,
        include_preview=include_preview,
        total=metrics.total,
        succeeded=metrics.succeeded,
        failed=metrics.failed,
        sessions=metrics.sessions,
        success_rate=metrics.success_rate,
        failure_rate=metrics.failure_rate,
        p50_latency_ms=metrics.p50_latency_ms,
        p95_latency_ms=metrics.p95_latency_ms,
    )
