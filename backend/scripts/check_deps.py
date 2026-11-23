"""检查 PostgreSQL / Redis / RabbitMQ 连通性。

用法：conda run -n agent-platform python scripts/check_deps.py
任一依赖不可用时退出码非 0。
"""

from __future__ import annotations

import asyncio
import sys

import aio_pika
import asyncpg
import redis.asyncio as aioredis

from agent_platform.settings import get_settings


def _pg_dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://")


async def check_postgres(dsn: str) -> str:
    conn = await asyncpg.connect(dsn=_pg_dsn(dsn), timeout=5)
    try:
        version = await conn.fetchval("select version()")
        return str(version).split(",")[0]
    finally:
        await conn.close()


async def check_redis(url: str) -> str:
    client = aioredis.from_url(url, socket_connect_timeout=5)
    try:
        pong = await client.ping()
        return f"PING -> {pong}"
    finally:
        await client.aclose()


async def check_rabbitmq(url: str) -> str:
    connection = await aio_pika.connect_robust(url=url, timeout=5)
    try:
        channel = await connection.channel()
        await channel.close()
        return "channel opened"
    finally:
        await connection.close()


async def main() -> int:
    settings = get_settings()
    checks = {
        "postgres": check_postgres(settings.database_url),
        "redis": check_redis(settings.redis_url),
        "rabbitmq": check_rabbitmq(settings.rabbitmq_url),
    }

    failed = 0
    for name, coro in checks.items():
        try:
            detail = await coro
            print(f"[OK]   {name}: {detail}")
        except Exception as exc:  # noqa: BLE001 - 诊断脚本需要打印所有失败原因
            failed += 1
            print(f"[FAIL] {name}: {type(exc).__name__}: {exc}")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
