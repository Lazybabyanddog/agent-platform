"""运行轨迹查询：按时间合并模型调用与工具调用，并标记最耗时步骤。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from agent_platform.contexts.execution.domain.enums import RunKind
from agent_platform.contexts.execution.infrastructure.repositories import RunRepository
from agent_platform.contexts.execution.infrastructure.tables import Run
from agent_platform.contexts.insight.infrastructure.repositories import (
    LLMCallRepository,
    ToolCallRepository,
)
from agent_platform.shared.db import session_scope
from agent_platform.shared.errors import NotFoundError


@dataclass(frozen=True, slots=True)
class TraceEntry:
    type: str
    trace_id: str
    span_id: str
    label: str
    status: str
    error_code: str | None
    started_at: datetime
    ended_at: datetime
    latency_ms: int
    summary: dict[str, Any]
    tokens: dict[str, int] | None
    content: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class TraceView:
    run: Run
    entries: list[TraceEntry]
    slowest_span_id: str | None
    slowest_latency_ms: int | None


class TraceService:
    async def list_runs(
        self,
        *,
        tenant_id: uuid.UUID,
        agent_id: uuid.UUID | None,
        kind: RunKind | None,
        start: datetime | None,
        end: datetime | None,
        page: int,
        page_size: int,
    ) -> tuple[list[Run], int]:
        async with session_scope(tenant_id) as session:
            return await RunRepository().list_runs(
                session,
                tenant_id=tenant_id,
                agent_id=agent_id,
                kind=kind,
                start=start,
                end=end,
                page=page,
                page_size=page_size,
            )

    async def get(
        self, *, tenant_id: uuid.UUID, run_id: uuid.UUID, actor_can_read_content: bool
    ) -> TraceView:
        async with session_scope(tenant_id) as session:
            run = await RunRepository().by_id(session, tenant_id=tenant_id, run_id=run_id)
            if run is None:
                raise NotFoundError("run not found")
            llm_calls = await LLMCallRepository().list_for_run(
                session, tenant_id=tenant_id, run_id=run_id
            )
            tool_calls = await ToolCallRepository().list_for_run(
                session, tenant_id=tenant_id, run_id=run_id
            )

        include_content = actor_can_read_content

        entries: list[TraceEntry] = [
            TraceEntry(
                type="LLM_CALL",
                trace_id=call.trace_id,
                span_id=call.span_id,
                label=call.model_name,
                status=str(call.status),
                error_code=call.error_code,
                started_at=call.started_at,
                ended_at=call.ended_at,
                latency_ms=call.latency_ms,
                summary={
                    **dict(call.request_summary or {}),
                    # 0 表示确实没带工具，None 表示该记录产生时尚未采集
                    "tool_count": call.tool_count,
                },
                tokens={"input": call.input_tokens, "output": call.output_tokens},
                content=(
                    {"request": call.request_body, "response": call.response_body}
                    if include_content
                    else None
                ),
            )
            for call in llm_calls
        ]
        entries.extend(
            TraceEntry(
                type="TOOL_CALL",
                trace_id=call.trace_id,
                span_id=call.span_id,
                label=call.tool_code,
                status=str(call.status),
                error_code=call.error_code,
                started_at=call.started_at,
                ended_at=call.ended_at,
                latency_ms=call.latency_ms,
                summary={
                    **dict(call.request_summary or {}),
                    "response": dict(call.response_summary or {}),
                },
                tokens=None,
                content=(
                    {"request": call.request_body, "response": call.response_body}
                    if include_content
                    else None
                ),
            )
            for call in tool_calls
        )
        entries.sort(key=lambda item: (item.started_at, item.span_id))

        slowest = max(entries, key=lambda item: item.latency_ms, default=None)
        return TraceView(
            run=run,
            entries=entries,
            slowest_span_id=slowest.span_id if slowest else None,
            slowest_latency_ms=slowest.latency_ms if slowest else None,
        )


__all__ = ["TraceEntry", "TraceService", "TraceView"]
