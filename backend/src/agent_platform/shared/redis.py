"""Redis 客户端的惰性单例。"""

from __future__ import annotations

from redis.asyncio import Redis, from_url

from agent_platform.settings import get_settings

_redis: Redis | None = None


def get_redis() -> Redis:
    global _redis
    if _redis is None:
        _redis = from_url(
            get_settings().redis_url,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
    return _redis


async def dispose_redis() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None
