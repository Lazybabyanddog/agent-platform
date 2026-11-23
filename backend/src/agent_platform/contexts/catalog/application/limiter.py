"""按租户 + 模型维度的限流。

两个维度：
- RPM：每分钟请求数，用 Redis 计数器 + 60s 过期窗口；
- 并发：同时在途的调用数，进入时 +1，退出时 -1。

Redis 不可用时直接抛出 UNAVAILABLE，不做"限流失效即放行"的静默降级。
"""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from redis.asyncio import Redis
from redis.exceptions import RedisError

from agent_platform.shared.errors import ModelRateLimitedError, UnavailableError
from agent_platform.shared.redis import get_redis


class ModelRateLimiter:
    def __init__(
        self, redis: Redis | None = None, clock: Callable[[], float] | None = None
    ) -> None:
        self._redis = redis
        self._clock = clock or time.time

    @property
    def redis(self) -> Redis:
        return self._redis or get_redis()

    @asynccontextmanager
    async def limit(
        self,
        *,
        tenant_id: uuid.UUID,
        model_code: str,
        rpm: int | None,
        max_concurrency: int | None,
    ) -> AsyncIterator[None]:
        client = self.redis
        scope = f"{tenant_id}:{model_code}"
        try:
            if rpm:
                window = int(self._clock() // 60)
                rpm_key = f"rl:rpm:{scope}:{window}"
                pipe = client.pipeline()
                pipe.incr(rpm_key)
                pipe.expire(rpm_key, 61)
                count, _ = await pipe.execute()
                if int(count) > rpm:
                    raise ModelRateLimitedError(
                        f"model {model_code} allows {rpm} requests per minute",
                        details=[
                            {
                                "field": "model.rpm",
                                "reason": "requests per minute limit reached",
                                "limit": rpm,
                            }
                        ],
                    )

            concurrency_key = f"rl:conc:{scope}"
            if max_concurrency:
                current = int(await client.incr(concurrency_key))
                await client.expire(concurrency_key, 120)
                if current > max_concurrency:
                    await client.decr(concurrency_key)
                    raise ModelRateLimitedError(
                        f"model {model_code} allows {max_concurrency} concurrent calls",
                        details=[
                            {
                                "field": "model.max_concurrency",
                                "reason": "concurrency limit reached",
                                "limit": max_concurrency,
                            }
                        ],
                    )
                try:
                    yield
                finally:
                    await client.decr(concurrency_key)
                return
        except RedisError as exc:  # pragma: no cover - 依赖故障分支
            raise UnavailableError("rate limiter unavailable") from exc

        yield


model_rate_limiter = ModelRateLimiter()
