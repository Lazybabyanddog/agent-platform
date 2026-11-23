"""运行指标聚合：调用量、成功率/失败率与延迟分位数。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from agent_platform.contexts.execution.domain.enums import RunKind
from agent_platform.contexts.insight.infrastructure.repositories import MetricsRepository
from agent_platform.shared.db import session_scope
from agent_platform.shared.errors import ValidationFailedError


@dataclass(frozen=True, slots=True)
class RunMetrics:
    agent_id: uuid.UUID | None
    window_start: datetime | None
    window_end: datetime | None
    total: int
    succeeded: int
    failed: int
    sessions: int
    success_rate: float
    failure_rate: float
    p50_latency_ms: float | None
    p95_latency_ms: float | None


class MetricsService:
    def __init__(self) -> None:
        self._repository = MetricsRepository()

    async def run_metrics(
        self,
        *,
        tenant_id: uuid.UUID,
        agent_id: uuid.UUID | None,
        start: datetime | None,
        end: datetime | None,
        kind: RunKind | None = None,
        include_preview: bool = False,
    ) -> RunMetrics:
        if start is not None and end is not None and start >= end:
            raise ValidationFailedError.for_field("start", "must be before end")

        async with session_scope(tenant_id) as session:
            summary = await self._repository.run_summary(
                session,
                tenant_id=tenant_id,
                agent_id=agent_id,
                start=start,
                end=end,
                kind=kind,
                include_preview=include_preview,
            )

        total = summary["total"]
        succeeded = summary["succeeded"]
        failed = summary["failed"]
        return RunMetrics(
            agent_id=agent_id,
            window_start=start,
            window_end=end,
            total=total,
            succeeded=succeeded,
            failed=failed,
            sessions=summary["sessions"],
            success_rate=round(succeeded / total, 4) if total else 0.0,
            failure_rate=round(failed / total, 4) if total else 0.0,
            p50_latency_ms=summary["p50_latency_ms"],
            p95_latency_ms=summary["p95_latency_ms"],
        )


__all__ = ["MetricsService", "RunMetrics"]
