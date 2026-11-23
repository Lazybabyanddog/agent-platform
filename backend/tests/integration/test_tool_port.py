"""ToolPort：执行期服务端二次授权 + 入参校验先于外部请求。"""

from __future__ import annotations

import uuid

import httpx

from agent_platform.contexts.catalog.application.tool_service import ToolService
from agent_platform.contexts.execution.infrastructure.tool_adapter import CatalogToolPort
from agent_platform.shared.context import ActorContext
from tests.integration.agent_helpers import create_tool_version
from tests.integration.conftest import TenantEnv


def _actor(tenant_env: TenantEnv) -> ActorContext:
    return ActorContext(
        tenant_id=tenant_env.tenant_id,
        actor_type="USER",
        actor_id=tenant_env.user_id,
    )


async def test_invalid_arguments_never_reach_upstream(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str]
) -> None:
    tool_version = await create_tool_version(api_client, admin_headers)
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"ok": True})

    port = CatalogToolPort(
        actor=_actor(tenant_env),
        service=ToolService(transport=httpx.MockTransport(handler)),
    )
    result = await port.invoke(
        tool_code=tool_version["tool_code"],
        tool_version_id=tool_version["id"],
        arguments={},
    )

    assert result.ok is False
    assert result.error_code == "INPUT_INVALID"
    assert calls == []


async def test_second_authorization_check_blocks_revoked_tool(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str]
) -> None:
    tool_version = await create_tool_version(api_client, admin_headers)
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"ok": True})

    port = CatalogToolPort(
        actor=_actor(tenant_env),
        service=ToolService(transport=httpx.MockTransport(handler)),
    )
    arguments = {"text": "hi"}

    allowed = await port.invoke(
        tool_code=tool_version["tool_code"],
        tool_version_id=tool_version["id"],
        arguments=arguments,
    )
    assert allowed.ok is True
    assert len(calls) == 1

    revoked = await api_client.delete(
        f"/api/v1/tools/{tool_version['tool_id']}/grant", headers=admin_headers
    )
    assert revoked.status_code == 204, revoked.text

    blocked = await port.invoke(
        tool_code=tool_version["tool_code"],
        tool_version_id=tool_version["id"],
        arguments=arguments,
    )
    assert blocked.ok is False
    assert blocked.error_code == "RESOURCE_UNAVAILABLE"
    assert len(calls) == 1


async def test_unknown_tool_version_is_reported_not_raised(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str]
) -> None:
    port = CatalogToolPort(actor=_actor(tenant_env))

    result = await port.invoke(
        tool_code="missing",
        tool_version_id=str(uuid.uuid4()),
        arguments={"text": "hi"},
    )

    assert result.ok is False
    assert result.error_code == "RESOURCE_UNAVAILABLE"
