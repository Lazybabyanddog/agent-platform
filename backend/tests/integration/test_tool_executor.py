"""工具执行器：入参校验、鉴权注入、超时、出参契约、风险等级。"""

from __future__ import annotations

import uuid

import httpx
import pytest

from agent_platform.contexts.catalog.application.tool_service import ToolService
from agent_platform.contexts.catalog.domain.enums import RiskLevel
from agent_platform.contexts.catalog.domain.errors import (
    HighRiskToolError,
    ToolExecutionError,
    ToolOutputInvalidError,
    ToolTimeoutError,
)
from agent_platform.contexts.platform.application.auth_service import auth_service
from agent_platform.contexts.platform.application.secret_service import secret_service
from agent_platform.shared.context import ActorContext
from agent_platform.shared.errors import ResourceUnavailableError, ValidationFailedError
from tests.integration.conftest import TenantEnv

SPEC = {
    "method": "POST",
    "url": "https://tools.local/echo",
    "timeout_s": 5,
    "input_schema": {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    },
    "output_schema": {
        "type": "object",
        "properties": {"ok": {"type": "boolean"}},
        "required": ["ok"],
    },
}


async def _actor(tenant_env: TenantEnv) -> ActorContext:
    return await auth_service.build_actor(
        tenant_id=tenant_env.tenant_id, user_id=tenant_env.user_id
    )


async def test_successful_call_records_digests(tenant_env: TenantEnv) -> None:
    actor = await _actor(tenant_env)
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    service = ToolService(transport=httpx.MockTransport(handler))
    tool, version = await service.create(
        actor=actor,
        code=f"echo-{uuid.uuid4().hex[:6]}",
        name="Echo",
        description=None,
        spec=SPEC,
        risk_level=RiskLevel.READ_ONLY,
    )
    await service.grant(actor=actor, tool_id=tool.id)

    result = await service.execute(
        actor=actor, tool_version_id=version.id, arguments={"text": "hi"}
    )
    assert result.status == "SUCCESS"
    assert result.output == {"ok": True}
    assert result.response_digest is not None
    assert len(result.request_digest) == 64
    assert seen[0].method == "POST"
    assert b'"text":"hi"' in seen[0].content.replace(b" ", b"")


async def test_invalid_arguments_never_reach_upstream(tenant_env: TenantEnv) -> None:
    actor = await _actor(tenant_env)
    calls: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"ok": True})

    service = ToolService(transport=httpx.MockTransport(handler))
    tool, version = await service.create(
        actor=actor,
        code=f"strict-{uuid.uuid4().hex[:6]}",
        name="Strict",
        description=None,
        spec=SPEC,
        risk_level=RiskLevel.READ_ONLY,
    )
    await service.grant(actor=actor, tool_id=tool.id)

    with pytest.raises(ValidationFailedError) as excinfo:
        await service.execute(actor=actor, tool_version_id=version.id, arguments={})
    assert calls == []
    assert excinfo.value.details[0]["field"].startswith("arguments")


async def test_ungranted_tool_is_blocked(tenant_env: TenantEnv) -> None:
    actor = await _actor(tenant_env)

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    service = ToolService(transport=httpx.MockTransport(handler))
    _tool, version = await service.create(
        actor=actor,
        code=f"nogrant-{uuid.uuid4().hex[:6]}",
        name="NoGrant",
        description=None,
        spec=SPEC,
        risk_level=RiskLevel.READ_ONLY,
    )
    with pytest.raises(ResourceUnavailableError):
        await service.execute(actor=actor, tool_version_id=version.id, arguments={"text": "x"})


async def test_output_contract_violation_and_http_error(tenant_env: TenantEnv) -> None:
    actor = await _actor(tenant_env)

    async def wrong_shape(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": "yes"})

    service = ToolService(transport=httpx.MockTransport(wrong_shape))
    tool, version = await service.create(
        actor=actor,
        code=f"shape-{uuid.uuid4().hex[:6]}",
        name="Shape",
        description=None,
        spec=SPEC,
        risk_level=RiskLevel.READ_ONLY,
    )
    await service.grant(actor=actor, tool_id=tool.id)

    with pytest.raises(ToolOutputInvalidError) as excinfo:
        await service.execute(actor=actor, tool_version_id=version.id, arguments={"text": "x"})
    assert excinfo.value.code == "TOOL_OUTPUT_INVALID"

    async def failing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream down")

    with pytest.raises(ToolExecutionError) as excinfo:
        await ToolService(transport=httpx.MockTransport(failing)).execute(
            actor=actor, tool_version_id=version.id, arguments={"text": "x"}
        )
    assert excinfo.value.details[0]["status"] == 503


async def test_timeout_is_reported_as_tool_timeout(tenant_env: TenantEnv) -> None:
    actor = await _actor(tenant_env)

    async def ok(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    service = ToolService(transport=httpx.MockTransport(ok))
    tool, version = await service.create(
        actor=actor,
        code=f"slow-{uuid.uuid4().hex[:6]}",
        name="Slow",
        description=None,
        spec=SPEC,
        risk_level=RiskLevel.READ_ONLY,
    )
    await service.grant(actor=actor, tool_id=tool.id)

    async def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    with pytest.raises(ToolTimeoutError) as excinfo:
        await ToolService(transport=httpx.MockTransport(slow)).execute(
            actor=actor, tool_version_id=version.id, arguments={"text": "x"}
        )
    assert excinfo.value.http_status == 504


async def test_secret_is_injected_as_authorization_header(tenant_env: TenantEnv) -> None:
    actor = await _actor(tenant_env)
    secret = await secret_service.create(
        actor=actor, name=f"tool-{uuid.uuid4().hex[:6]}", value="token-abc"
    )
    captured: dict[str, str] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("authorization", "")
        captured["body"] = request.content.decode()
        return httpx.Response(200, json={"ok": True})

    service = ToolService(transport=httpx.MockTransport(handler))
    tool, version = await service.create(
        actor=actor,
        code=f"auth-{uuid.uuid4().hex[:6]}",
        name="Auth",
        description=None,
        spec={
            **SPEC,
            "auth": {"type": "bearer", "secret_ref_id": str(secret.id)},
        },
        risk_level=RiskLevel.READ_ONLY,
    )
    await service.grant(actor=actor, tool_id=tool.id)
    await service.execute(actor=actor, tool_version_id=version.id, arguments={"text": "x"})

    assert captured["authorization"] == "Bearer token-abc"
    assert "token-abc" not in captured["body"]


async def test_disabled_tool_is_blocked_and_recovers_after_enable(tenant_env: TenantEnv) -> None:
    from agent_platform.contexts.catalog.domain.enums import ToolStatus

    actor = await _actor(tenant_env)

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    service = ToolService(transport=httpx.MockTransport(handler))
    tool, version = await service.create(
        actor=actor,
        code=f"toggle-{uuid.uuid4().hex[:6]}",
        name="Toggle",
        description=None,
        spec=SPEC,
        risk_level=RiskLevel.READ_ONLY,
    )
    await service.grant(actor=actor, tool_id=tool.id)

    await service.set_status(actor=actor, tool_id=tool.id, status=ToolStatus.DISABLED)
    with pytest.raises(ResourceUnavailableError):
        await service.execute(actor=actor, tool_version_id=version.id, arguments={"text": "x"})

    await service.set_status(actor=actor, tool_id=tool.id, status=ToolStatus.ACTIVE)
    result = await service.execute(
        actor=actor, tool_version_id=version.id, arguments={"text": "x"}
    )
    assert result.status == "SUCCESS"


async def test_high_risk_tool_requires_explicit_approval(tenant_env: TenantEnv) -> None:
    actor = await _actor(tenant_env)

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    service = ToolService(transport=httpx.MockTransport(handler))
    tool, version = await service.create(
        actor=actor,
        code=f"high-{uuid.uuid4().hex[:6]}",
        name="High",
        description=None,
        spec={**SPEC, "output_schema": None},
        risk_level=RiskLevel.HIGH,
    )
    await service.grant(actor=actor, tool_id=tool.id)

    with pytest.raises(HighRiskToolError):
        await service.execute(actor=actor, tool_version_id=version.id, arguments={"text": "x"})

    result = await service.execute(
        actor=actor,
        tool_version_id=version.id,
        arguments={"text": "x"},
        allow_high_risk=True,
    )
    assert result.status == "SUCCESS"
