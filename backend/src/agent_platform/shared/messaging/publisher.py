"""Outbox 搬运进程。

一轮循环 = 取一批待发事件（SKIP LOCKED，可多实例并行）-> 逐条投递 -> 更新状态。
投递失败按指数退避重试，超过 ``max_attempts`` 标记 FAILED 但保留记录，便于人工重放。
"""

from __future__ import annotations

import asyncio
import json
from typing import Protocol

import aio_pika
from sqlalchemy.ext.asyncio import AsyncSession

from agent_platform.shared.db import system_session_scope
from agent_platform.shared.logging import get_logger
from agent_platform.shared.messaging.envelope import EXCHANGE_NAME
from agent_platform.shared.messaging.outbox import (
    claim_due_batch,
    mark_attempt_failed,
    mark_sent,
)

logger = get_logger(__name__)


class EventSink(Protocol):
    async def publish(self, *, routing_key: str, body: str, message_id: str) -> None: ...

    async def close(self) -> None: ...


class RabbitMQEventSink:
    def __init__(self, url: str, exchange_name: str = EXCHANGE_NAME) -> None:
        self._url = url
        self._exchange_name = exchange_name
        self._connection: aio_pika.abc.AbstractRobustConnection | None = None
        self._channel: aio_pika.abc.AbstractRobustChannel | None = None
        self._exchange: aio_pika.abc.AbstractRobustExchange | None = None
        self._lock = asyncio.Lock()

    async def _ensure_exchange(self) -> aio_pika.abc.AbstractRobustExchange:
        async with self._lock:
            if self._exchange is None:
                self._connection = await aio_pika.connect_robust(self._url)
                self._channel = await self._connection.channel()
                self._exchange = await self._channel.declare_exchange(
                    self._exchange_name, aio_pika.ExchangeType.TOPIC, durable=True
                )
            return self._exchange

    async def publish(self, *, routing_key: str, body: str, message_id: str) -> None:
        exchange = await self._ensure_exchange()
        await exchange.publish(
            aio_pika.Message(
                body=body.encode("utf-8"),
                content_type="application/json",
                message_id=message_id,
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            ),
            routing_key=routing_key,
        )

    async def close(self) -> None:
        if self._connection is not None:
            await self._connection.close()
        self._connection = None
        self._channel = None
        self._exchange = None


class OutboxPublisher:
    def __init__(
        self,
        sink: EventSink,
        *,
        batch_size: int = 20,
        max_attempts: int = 10,
        poll_interval_s: float = 1.0,
    ) -> None:
        self._sink = sink
        self._batch_size = batch_size
        self._max_attempts = max_attempts
        self._poll_interval_s = poll_interval_s

    async def publish_once(self, *, session: AsyncSession | None = None) -> int:
        if session is not None:
            return await self._publish_batch(session)
        async with system_session_scope() as own_session:
            return await self._publish_batch(own_session)

    async def _publish_batch(self, session: AsyncSession) -> int:
        events = await claim_due_batch(session, limit=self._batch_size)
        sent = 0
        for event in events:
            try:
                await self._sink.publish(
                    routing_key=event.event_type,
                    body=json.dumps(event.payload, ensure_ascii=False),
                    message_id=str(event.id),
                )
            except Exception as exc:  # noqa: BLE001 - 任何投递异常都要落库重试
                logger.warning("outbox.publish_failed", event_id=str(event.id))
                await mark_attempt_failed(
                    session,
                    event,
                    error=f"{type(exc).__name__}: {exc}",
                    max_attempts=self._max_attempts,
                )
            else:
                await mark_sent(session, event)
                sent += 1
        return sent

    async def run_forever(self, *, stop: asyncio.Event | None = None) -> None:
        stop = stop or asyncio.Event()
        try:
            while not stop.is_set():
                try:
                    published = await self.publish_once()
                except Exception:  # noqa: BLE001 - 进程不能因为一次数据库抖动退出
                    logger.exception("outbox.loop_failed")
                    published = 0
                if published == 0:
                    try:
                        await asyncio.wait_for(stop.wait(), timeout=self._poll_interval_s)
                    except TimeoutError:
                        pass
        finally:
            await self._sink.close()
