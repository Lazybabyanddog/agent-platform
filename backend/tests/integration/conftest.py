"""集成测试共用的租户环境。

每个用例一个独立租户，结束后按租户清理，避免互相看到对方的数据。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx
import pytest
from sqlalchemy import text

from agent_platform.bootstrap import bootstrap
from agent_platform.main import create_app
from agent_platform.shared.db import system_session_scope

ADMIN_PASSWORD = "Passw0rd!123"


@dataclass(frozen=True, slots=True)
class TenantEnv:
    tenant_id: uuid.UUID
    tenant_code: str
    email: str
    user_id: uuid.UUID
    password: str


async def purge_tenant(*, tenant_id: uuid.UUID, user_id: uuid.UUID | None = None) -> None:
    """清理租户及其无外键约束的附属数据（audit_log / user_role / resource_grant）。"""
    async with system_session_scope() as session:
        for statement in (
            "delete from insight.llm_call where tenant_id = :tid",
            "delete from insight.tool_call where tenant_id = :tid",
            "delete from execution.checkpoint_ref where tenant_id = :tid",
            "delete from execution.execution_event where tenant_id = :tid",
            "delete from execution.run where tenant_id = :tid",
            "delete from execution.execution_session where tenant_id = :tid",
            "delete from platform.audit_log where tenant_id = :tid",
            "delete from platform.resource_grant where tenant_id = :tid",
            "delete from platform.user_role where tenant_id = :tid",
            "delete from platform.outbox_event where tenant_id = :tid",
        ):
            await session.execute(text(statement), {"tid": tenant_id})
        await session.execute(text("delete from platform.tenant where id = :tid"), {"tid": tenant_id})
        if user_id is not None:
            await session.execute(
                text("delete from platform.user_account where id = :uid"), {"uid": user_id}
            )


@pytest.fixture()
async def api_client() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest.fixture()
async def tenant_env() -> AsyncIterator[TenantEnv]:
    suffix = uuid.uuid4().hex[:10]
    env = TenantEnv(
        tenant_id=uuid.uuid4(),  # 占位，下面替换为真实值
        tenant_code=f"t{suffix}",
        email=f"admin-{suffix}@example.com",
        user_id=uuid.uuid4(),
        password=ADMIN_PASSWORD,
    )
    result = await bootstrap(
        tenant_code=env.tenant_code,
        tenant_name=f"Test Tenant {suffix}",
        admin_email=env.email,
        admin_password=env.password,
    )
    env = TenantEnv(
        tenant_id=result.tenant_id,
        tenant_code=env.tenant_code,
        email=env.email,
        user_id=result.user_id,
        password=env.password,
    )
    yield env
    await purge_tenant(tenant_id=env.tenant_id, user_id=env.user_id)


@pytest.fixture()
async def admin_headers(api_client: httpx.AsyncClient, tenant_env: TenantEnv) -> dict[str, str]:
    response = await api_client.post(
        "/api/v1/auth/login",
        json={
            "tenant_code": tenant_env.tenant_code,
            "email": tenant_env.email,
            "password": tenant_env.password,
        },
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}
