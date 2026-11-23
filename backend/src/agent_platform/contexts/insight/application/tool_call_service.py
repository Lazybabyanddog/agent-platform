"""工具维度调用概览：窗口内调用量、成功率、平均耗时与最近条目。

只返回摘要字段，不读出 request_body / response_body：
工具调用视图面向 TOOL_READ，正文属于运行轨迹的 RUN_READ + 留存策略。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from agent_platform.contexts.insight.infrastructure.repositories import ToolCallRepository
from agent_platform.shared.db import session_scope
from agent_platform.shared.errors import ValidationFailedError

DEFAULT_WINDOW_DAYS = 7
DEFAULT_LIMIT = 20
MAX_LIMIT = 100


@dataclass(frozen=True, slots=True)
class ToolCallItem:
    id: uuid.UUID
    tool_id: uuid.UUID | None
    tool_version_id: uuid.UUID
    status: str
    error_code: str | None
    latency_ms: int
    started_at: datetime


@dataclass(frozen=True, slots=True)
class ToolCallOverview:
    tool_id: uuid.UUID
    window_start: datetime
    window_end: datetime
    total: int
    succeeded: int
    failed: int
    success_rate: float | None
    avg_latency_ms: float | None
    errors: list[tuple[str, int]]
    items: list[ToolCallItem]


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class ToolCallService:
    def __init__(self) -> None:
        self._repository = ToolCallRepository()

    async def overview(
        self,
        *,
        tenant_id: uuid.UUID,
        tool_id: uuid.UUID,
        tool_version_id: uuid.UUID | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = DEFAULT_LIMIT,
    ) -> ToolCallOverview:
        window_end = _aware(end) if end is not None else datetime.now(UTC)
        window_start = (
            _aware(start)
            if start is not None
            else window_end - timedelta(days=DEFAULT_WINDOW_DAYS)
        )
        if window_start >= window_end:
            raise ValidationFailedError.for_field("start", "must be before end")
        effective_limit = min(max(limit, 1), MAX_LIMIT)

        async with session_scope(tenant_id) as session:
            summary = await self._repository.summary_for_tool(
                session,
                tenant_id=tenant_id,
                tool_id=tool_id,
                tool_version_id=tool_version_id,
                start=window_start,
                end=window_end,
            )
            errors = await self._repository.error_counts_for_tool(
                session,
                tenant_id=tenant_id,
                tool_id=tool_id,
                tool_version_id=tool_version_id,
                start=window_start,
                end=window_end,
            )
            calls = await self._repository.list_for_tool(
                session,
                tenant_id=tenant_id,
                tool_id=tool_id,
                tool_version_id=tool_version_id,
                start=window_start,
                end=window_end,
                limit=effective_limit,
            )

        total = summary["total"]
        return ToolCallOverview(
            tool_id=tool_id,
            window_start=window_start,
            window_end=window_end,
            total=total,
            succeeded=summary["succeeded"],
            failed=summary["failed"],
            # total == 0 时为 None，让前端区分"无数据"与"全失败"
            success_rate=round(summary["succeeded"] / total, 4) if total else None,
            avg_latency_ms=summary["avg_latency_ms"],
            errors=errors,
            items=[
                ToolCallItem(
                    id=call.id,
                    tool_id=call.tool_id,
                    tool_version_id=call.tool_version_id,
                    status=str(call.status),
                    error_code=call.error_code,
                    latency_ms=call.latency_ms,
                    started_at=call.started_at,
                )
                for call in calls
            ],
        )


tool_call_service = ToolCallService()

__all__ = [
    "DEFAULT_LIMIT",
    "DEFAULT_WINDOW_DAYS",
    "MAX_LIMIT",
    "ToolCallItem",
    "ToolCallOverview",
    "ToolCallService",
    "tool_call_service",
]
