"""insight 数据访问：调用轨迹写入/读取与运行指标聚合。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_platform.contexts.execution.domain.enums import RunKind, RunStatus
from agent_platform.contexts.execution.infrastructure.tables import Run
from agent_platform.contexts.insight.domain.enums import CallStatus
from agent_platform.contexts.insight.infrastructure.tables import LLMCall, ToolCall


def _tool_call_conditions(
    *,
    tenant_id: uuid.UUID,
    tool_id: uuid.UUID,
    tool_version_id: uuid.UUID | None,
    start: datetime | None,
    end: datetime | None,
) -> list[Any]:
    conditions: list[Any] = [ToolCall.tenant_id == tenant_id, ToolCall.tool_id == tool_id]
    if tool_version_id is not None:
        conditions.append(ToolCall.tool_version_id == tool_version_id)
    if start is not None:
        conditions.append(ToolCall.started_at >= start)
    if end is not None:
        conditions.append(ToolCall.started_at < end)
    return conditions


class LLMCallRepository:
    async def create(self, session: AsyncSession, **values: Any) -> LLMCall:
        call = LLMCall(**values)
        session.add(call)
        await session.flush()
        return call

    async def list_for_run(
        self, session: AsyncSession, *, tenant_id: uuid.UUID, run_id: uuid.UUID
    ) -> list[LLMCall]:
        result = await session.scalars(
            select(LLMCall)
            .where(LLMCall.tenant_id == tenant_id, LLMCall.run_id == run_id)
            .order_by(LLMCall.started_at, LLMCall.span_id)
        )
        return list(result)


class ToolCallRepository:
    async def create(self, session: AsyncSession, **values: Any) -> ToolCall:
        call = ToolCall(**values)
        session.add(call)
        await session.flush()
        return call

    async def list_for_run(
        self, session: AsyncSession, *, tenant_id: uuid.UUID, run_id: uuid.UUID
    ) -> list[ToolCall]:
        result = await session.scalars(
            select(ToolCall)
            .where(ToolCall.tenant_id == tenant_id, ToolCall.run_id == run_id)
            .order_by(ToolCall.started_at, ToolCall.span_id)
        )
        return list(result)

    async def list_for_tool(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        tool_id: uuid.UUID,
        tool_version_id: uuid.UUID | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 20,
    ) -> list[ToolCall]:
        conditions = _tool_call_conditions(
            tenant_id=tenant_id,
            tool_id=tool_id,
            tool_version_id=tool_version_id,
            start=start,
            end=end,
        )
        result = await session.scalars(
            select(ToolCall)
            .where(*conditions)
            .order_by(ToolCall.started_at.desc(), ToolCall.span_id.desc())
            .limit(limit)
        )
        return list(result)

    async def summary_for_tool(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        tool_id: uuid.UUID,
        tool_version_id: uuid.UUID | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> dict[str, Any]:
        conditions = _tool_call_conditions(
            tenant_id=tenant_id,
            tool_id=tool_id,
            tool_version_id=tool_version_id,
            start=start,
            end=end,
        )
        statement = select(
            func.count().label("total"),
            func.count().filter(ToolCall.status == CallStatus.SUCCESS).label("succeeded"),
            func.count().filter(ToolCall.status == CallStatus.FAILED).label("failed"),
            func.avg(ToolCall.latency_ms).label("avg_latency"),
        ).where(*conditions)
        row = (await session.execute(statement)).one()
        return {
            "total": int(row.total or 0),
            "succeeded": int(row.succeeded or 0),
            "failed": int(row.failed or 0),
            "avg_latency_ms": float(row.avg_latency) if row.avg_latency is not None else None,
        }

    async def error_counts_for_tool(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        tool_id: uuid.UUID,
        tool_version_id: uuid.UUID | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[tuple[str, int]]:
        conditions = [
            *_tool_call_conditions(
                tenant_id=tenant_id,
                tool_id=tool_id,
                tool_version_id=tool_version_id,
                start=start,
                end=end,
            ),
            ToolCall.error_code.is_not(None),
        ]
        count_label = func.count().label("count")
        statement = (
            select(ToolCall.error_code, count_label)
            .where(*conditions)
            .group_by(ToolCall.error_code)
            .order_by(count_label.desc(), ToolCall.error_code)
        )
        rows = await session.execute(statement)
        return [(str(code), int(count)) for code, count in rows.all()]


class MetricsRepository:
    """按租户/Agent 聚合运行指标，数据来源为 execution.run（只读）。"""

    async def run_summary(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        agent_id: uuid.UUID | None,
        start: datetime | None,
        end: datetime | None,
        kind: RunKind | None = None,
        include_preview: bool = False,
    ) -> dict[str, Any]:
        latency = func.extract("epoch", Run.finished_at - Run.started_at) * 1000.0
        conditions = [Run.tenant_id == tenant_id]
        if agent_id is not None:
            conditions.append(Run.agent_id == agent_id)
        if start is not None:
            conditions.append(Run.started_at >= start)
        if end is not None:
            conditions.append(Run.started_at < end)
        # 默认口径只统计正式调用；显式指定 kind 或 include_preview 才纳入预览
        if kind is not None:
            conditions.append(Run.kind == kind)
        elif not include_preview:
            conditions.append(Run.kind == RunKind.INVOKE)

        statement = select(
            func.count().label("total"),
            func.count().filter(Run.status == RunStatus.SUCCESS).label("succeeded"),
            func.count().filter(Run.status == RunStatus.FAILED).label("failed"),
            func.count(func.distinct(Run.execution_session_id)).label("sessions"),
            func.percentile_cont(0.5).within_group(latency).label("p50"),
            func.percentile_cont(0.95).within_group(latency).label("p95"),
        ).where(*conditions)
        row = (await session.execute(statement)).one()
        return {
            "total": int(row.total or 0),
            "succeeded": int(row.succeeded or 0),
            "failed": int(row.failed or 0),
            "sessions": int(row.sessions or 0),
            "p50_latency_ms": float(row.p50) if row.p50 is not None else None,
            "p95_latency_ms": float(row.p95) if row.p95 is not None else None,
        }
