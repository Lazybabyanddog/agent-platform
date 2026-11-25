"""Outbox 搬运进程入口：``python -m agent_platform.workers.outbox``。"""

from __future__ import annotations

import asyncio

from agent_platform.settings import get_settings
from agent_platform.shared.db import dispose_engines
from agent_platform.shared.logging import configure_logging, get_logger
from agent_platform.shared.messaging.publisher import OutboxPublisher, RabbitMQEventSink

logger = get_logger(__name__)


async def main() -> None:
    settings = get_settings()
    configure_logging(settings, service="outbox")
    if not settings.outbox_publisher_enabled:
        logger.warning("outbox.publish_disabled")
        return

    publisher = OutboxPublisher(
        RabbitMQEventSink(settings.rabbitmq_url, exchange_name=settings.events_exchange),
        batch_size=settings.outbox_batch_size,
        poll_interval_s=settings.outbox_poll_interval_s,
    )
    logger.info("outbox.started", exchange=settings.events_exchange)
    try:
        await publisher.run_forever()
    finally:
        await dispose_engines()


if __name__ == "__main__":
    asyncio.run(main())
