"""模型限流：按租户 + 模型维度，RPM 与并发两条线。"""

from __future__ import annotations

import uuid

import pytest

from agent_platform.contexts.catalog.application.limiter import ModelRateLimiter
from agent_platform.shared.errors import ModelRateLimitedError
from agent_platform.shared.redis import get_redis
from tests.integration.conftest import TenantEnv


async def test_rpm_limit_recovers_after_window(tenant_env: TenantEnv) -> None:
    now = [1_700_000_000.0]
    limiter = ModelRateLimiter(clock=lambda: now[0])
    model_code = f"rpm-{uuid.uuid4().hex[:6]}"

    async with limiter.limit(
        tenant_id=tenant_env.tenant_id, model_code=model_code, rpm=2, max_concurrency=None
    ):
        pass
    async with limiter.limit(
        tenant_id=tenant_env.tenant_id, model_code=model_code, rpm=2, max_concurrency=None
    ):
        pass

    with pytest.raises(ModelRateLimitedError) as excinfo:
        async with limiter.limit(
            tenant_id=tenant_env.tenant_id, model_code=model_code, rpm=2, max_concurrency=None
        ):
            pass
    assert excinfo.value.http_status == 429
    # 与模型自身错误区分开
    assert excinfo.value.code == "MODEL_RATE_LIMITED"

    now[0] += 61
    async with limiter.limit(
        tenant_id=tenant_env.tenant_id, model_code=model_code, rpm=2, max_concurrency=None
    ):
        pass


async def test_concurrency_limit_releases_slot(tenant_env: TenantEnv) -> None:
    limiter = ModelRateLimiter()
    model_code = f"conc-{uuid.uuid4().hex[:6]}"

    async with limiter.limit(
        tenant_id=tenant_env.tenant_id, model_code=model_code, rpm=None, max_concurrency=1
    ):
        with pytest.raises(ModelRateLimitedError):
            async with limiter.limit(
                tenant_id=tenant_env.tenant_id, model_code=model_code, rpm=None, max_concurrency=1
            ):
                pass

    # 退出后额度归还
    async with limiter.limit(
        tenant_id=tenant_env.tenant_id, model_code=model_code, rpm=None, max_concurrency=1
    ):
        pass

    assert await get_redis().get(f"rl:conc:{tenant_env.tenant_id}:{model_code}") == "0"


async def test_limits_are_isolated_per_tenant(tenant_env: TenantEnv) -> None:
    now = [1_700_000_100.0]
    limiter = ModelRateLimiter(clock=lambda: now[0])
    model_code = f"shared-{uuid.uuid4().hex[:6]}"

    async with limiter.limit(
        tenant_id=tenant_env.tenant_id, model_code=model_code, rpm=1, max_concurrency=None
    ):
        pass

    # 另一个租户同名模型不受影响
    async with limiter.limit(
        tenant_id=uuid.uuid4(), model_code=model_code, rpm=1, max_concurrency=None
    ):
        pass
