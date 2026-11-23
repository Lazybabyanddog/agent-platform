"""Outbox：发布事件与业务同事务落库，由搬运进程投递到 RabbitMQ。"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import aio_pika
from sqlalchemy import text

from agent_platform.settings import get_settings
from agent_platform.shared.db import session_scope, system_session_scope
from agent_platform.shared.messaging.envelope import EXCHANGE_NAME
from agent_platform.shared.messaging.outbox import record_event
from agent_platform.shared.messaging.publisher import OutboxPublisher, RabbitMQEventSink
from tests.integration.agent_helpers import (
    create_agent,
    create_agent_version,
    create_model,
    create_prompt_version,
)
from tests.integration.conftest import TenantEnv

RELEASE_EVENT = "agent.release.published"


class FailingSink:
    async def publish(self, *, routing_key: str, body: str, message_id: str) -> None:
        raise RuntimeError("broker down")

    async def close(self) -> None:
        return None


@asynccontextmanager
async def _bound_queue(routing_key: str) -> AsyncIterator[Any]:
    connection = await aio_pika.connect_robust(get_settings().rabbitmq_url)
    channel = await connection.channel()
    exchange = await channel.declare_exchange(
        EXCHANGE_NAME, aio_pika.ExchangeType.TOPIC, durable=True
    )
    queue = await channel.declare_queue("", exclusive=True)
    await queue.bind(exchange, routing_key=routing_key)
    try:
        yield queue
    finally:
        await queue.delete(if_unused=False, if_empty=False)
        await connection.close()


def _decode_payload(value: Any) -> dict[str, Any]:
    return json.loads(value) if isinstance(value, str) else value


async def _receive(queue: Any, expected: int) -> list[dict[str, Any]]:
    bodies: list[dict[str, Any]] = []
    for _ in range(expected):
        message = await queue.get(timeout=10, fail=True)
        async with message.process():
            bodies.append(json.loads(message.body))
    return bodies


async def test_release_event_is_stored_then_delivered_to_rabbitmq(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str]
) -> None:
    model = await create_model(api_client, admin_headers)
    prompt_version = await create_prompt_version(api_client, admin_headers)
    agent = await create_agent(api_client, admin_headers)
    version = await create_agent_version(
        api_client,
        admin_headers,
        agent["id"],
        model_id=model["id"],
        prompt_version_id=prompt_version["id"],
    )
    published = await api_client.post(
        f"/api/v1/agents/{agent['id']}/releases",
        headers=admin_headers,
        json={"agent_version_id": version["id"], "env": "DEV"},
    )
    assert published.status_code == 201, published.text

    async with system_session_scope() as session:
        rows = (
            await session.execute(
                text(
                    "select id, payload, status, attempts from platform.outbox_event "
                    "where tenant_id = :tid and event_type = :etype"
                ),
                {"tid": tenant_env.tenant_id, "etype": RELEASE_EVENT},
            )
        ).all()
    assert len(rows) == 1
    outbox_id, payload, status, attempts = rows[0]
    envelope = _decode_payload(payload)
    assert status == "PENDING"
    assert attempts == 0
    assert envelope["event_type"] == RELEASE_EVENT
    assert envelope["tenant_id"] == str(tenant_env.tenant_id)
    assert envelope["payload"]["release_id"] == published.json()["release_id"]
    assert envelope["payload"]["env"] == "DEV"

    async with _bound_queue(RELEASE_EVENT) as queue:
        sink = RabbitMQEventSink(get_settings().rabbitmq_url)
        publisher = OutboxPublisher(sink, batch_size=10)
        try:
            sent = await publisher.publish_once()
        finally:
            await sink.close()
        assert sent == 1

        messages = await _receive(queue, 1)
        assert messages[0]["event_id"] == envelope["event_id"]

    async with system_session_scope() as session:
        row = (
            await session.execute(
                text(
                    "select status, attempts, sent_at from platform.outbox_event where id = :eid"
                ),
                {"eid": outbox_id},
            )
        ).one()
    assert row.status == "SENT"
    assert row.attempts == 1
    assert row.sent_at is not None


async def test_failed_delivery_keeps_event_and_retries(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str]
) -> None:
    async with session_scope(tenant_env.tenant_id) as session:
        event = await record_event(
            session,
            tenant_id=tenant_env.tenant_id,
            event_type=RELEASE_EVENT,
            payload={"agent_id": str(uuid.uuid4()), "note": "retry-me"},
        )
        event_id = event.id
        envelope_id = event.payload["event_id"]

    failing = OutboxPublisher(FailingSink(), batch_size=10)
    assert await failing.publish_once() == 0

    async with system_session_scope() as session:
        row = (
            await session.execute(
                text(
                    "select status, attempts, last_error from platform.outbox_event where id = :eid"
                ),
                {"eid": event_id},
            )
        ).one()
        assert row.status == "PENDING"
        assert row.attempts == 1
        assert "broker down" in row.last_error
        await session.execute(
            text(
                "update platform.outbox_event "
                "set next_attempt_at = now() - interval '1 second' where id = :eid"
            ),
            {"eid": event_id},
        )

    async with _bound_queue(RELEASE_EVENT) as queue:
        sink = RabbitMQEventSink(get_settings().rabbitmq_url)
        publisher = OutboxPublisher(sink, batch_size=10)
        try:
            sent = await publisher.publish_once()
        finally:
            await sink.close()
        assert sent == 1
        messages = await _receive(queue, 1)
        assert messages[0]["event_id"] == envelope_id

    async with system_session_scope() as session:
        row = (
            await session.execute(
                text("select status, attempts from platform.outbox_event where id = :eid"),
                {"eid": event_id},
            )
        ).one()
    assert row.status == "SENT"
    assert row.attempts == 2
