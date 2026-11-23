"""Outbox 写入与投递状态流转。

业务事务里只写 outbox 行（与业务数据同一个事务），再由独立进程搬运到 RabbitMQ：
- 写库成功但发布失败不会丢事件；
- 极端情况下可能重复投递，消费方需按 event_id 幂等。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_platform.contexts.platform.domain.enums import OutboxStatus
from agent_platform.contexts.platform.infrastructure.tables import OutboxEvent
from agent_platform.shared.messaging.envelope import EventEnvelope, utcnow

MAX_BACKOFF_S = 300


async def record_event(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    event_type: str,
    payload: dict[str, Any],
    trace_id: str | None = None,
    event_id: uuid.UUID | None = None,
) -> OutboxEvent:
    from agent_platform.shared.ids import new_id

    envelope = EventEnvelope(
        event_id=event_id or new_id(),
        event_type=event_type,
        tenant_id=tenant_id,
        occurred_at=utcnow(),
        payload=payload,
        trace_id=trace_id,
    )
    event = OutboxEvent(
        tenant_id=tenant_id,
        event_type=event_type,
        payload=envelope.to_dict(),
        status=OutboxStatus.PENDING,
        attempts=0,
    )
    session.add(event)
    await session.flush()
    return event


async def claim_due_batch(
    session: AsyncSession, *, limit: int = 20, now: datetime | None = None
) -> list[OutboxEvent]:
    now = now or utcnow()
    stmt = (
        select(OutboxEvent)
        .where(
            OutboxEvent.status == OutboxStatus.PENDING,
            or_(OutboxEvent.next_attempt_at.is_(None), OutboxEvent.next_attempt_at <= now),
        )
        .order_by(OutboxEvent.created_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    return list(await session.scalars(stmt))


async def mark_sent(session: AsyncSession, event: OutboxEvent, *, now: datetime | None = None) -> None:
    event.status = OutboxStatus.SENT
    event.sent_at = now or utcnow()
    event.attempts += 1
    event.last_error = None


async def mark_attempt_failed(
    session: AsyncSession,
    event: OutboxEvent,
    *,
    error: str,
    max_attempts: int,
    now: datetime | None = None,
) -> None:
    now = now or utcnow()
    event.attempts += 1
    event.last_error = error[:2000]
    if event.attempts >= max_attempts:
        event.status = OutboxStatus.FAILED
        return
    backoff = min(2 ** event.attempts, MAX_BACKOFF_S)
    event.next_attempt_at = now + timedelta(seconds=backoff)
