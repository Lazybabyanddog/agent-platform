"""MCP 工具执行：与 HTTP 共用入参/鉴权/超时/出参骨架，失败收敛为稳定错误码。"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from agent_platform.contexts.catalog.application.tool_service import ToolService
from agent_platform.contexts.catalog.domain.enums import RiskLevel
from agent_platform.contexts.catalog.domain.errors import (
    MCP_ERROR_SUMMARY_LIMIT,
    HighRiskToolError,
    ToolMcpError,
    ToolOutputInvalidError,
    ToolTimeoutError,
)
from agent_platform.contexts.catalog.infrastructure.mcp_client import McpCallResult
from agent_platform.contexts.platform.application.auth_service import auth_service
from agent_platform.contexts.platform.application.secret_service import secret_service
from agent_platform.shared.context import ActorContext
from agent_platform.shared.errors import ResourceUnavailableError, ValidationFailedError
from tests.integration.conftest import TenantEnv

MCP_SPEC: dict[str, Any] = {
    "transport": "streamable_http",
    "url": "https://knowledge.example.com/mcp",
    "tool_name": "search_documents",
    "timeout_s": 5,
    "input_schema": {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    },
    "output_schema": {
        "type": "object",
        "properties": {"hits": {"type": "integer"}},
        "required": ["hits"],
    },
}


class FakeMcpSession:
    """记录调用参数，返回预设结果或抛出预设异常。"""

    def __init__(self, *, result: McpCallResult | None = None, error: Exception | None = None):
        self.result = result
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def call_tool(self, **kwargs: Any) -> McpCallResult:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


async def _actor(tenant_env: TenantEnv) -> ActorContext:
    return await auth_service.build_actor(
        tenant_id=tenant_env.tenant_id, user_id=tenant_env.user_id
    )


async def _granted_tool(
    actor: ActorContext,
    *,
    spec: dict[str, Any],
    session: FakeMcpSession,
    risk_level: RiskLevel = RiskLevel.READ_ONLY,
) -> tuple[ToolService, uuid.UUID]:
    service = ToolService(mcp_session=session)
    tool, version = await service.create(
        actor=actor,
        code=f"mcp-{uuid.uuid4().hex[:6]}",
        name="Knowledge Search",
        description=None,
        spec=spec,
        risk_level=risk_level,
        type="MCP",
    )
    await service.grant(actor=actor, tool_id=tool.id)
    return service, version.id


async def test_successful_mcp_call_passes_contract_and_records_digests(
    tenant_env: TenantEnv,
) -> None:
    actor = await _actor(tenant_env)
    session = FakeMcpSession(
        result=McpCallResult(output={"hits": 3}, remote_tool_name="search_documents")
    )
    service, version_id = await _granted_tool(actor, spec=MCP_SPEC, session=session)

    result = await service.execute(
        actor=actor, tool_version_id=version_id, arguments={"query": "refund"}
    )

    assert result.status == "SUCCESS"
    assert result.output == {"hits": 3}
    assert len(result.request_digest) == 64
    assert result.response_digest is not None
    call = session.calls[0]
    assert call["url"] == "https://knowledge.example.com/mcp"
    assert call["transport"] == "streamable_http"
    assert call["tool_name"] == "search_documents"
    assert call["arguments"] == {"query": "refund"}
    assert call["timeout_s"] == 5.0


async def test_remote_is_error_is_reported_as_tool_mcp_error(tenant_env: TenantEnv) -> None:
    actor = await _actor(tenant_env)
    session = FakeMcpSession(
        result=McpCallResult(
            output=None,
            remote_tool_name="search_documents",
            is_error=True,
            error_summary="upstream quota exhausted: " + "x" * 900,
        )
    )
    service, version_id = await _granted_tool(actor, spec=MCP_SPEC, session=session)

    with pytest.raises(ToolMcpError) as excinfo:
        await service.execute(actor=actor, tool_version_id=version_id, arguments={"query": "q"})

    assert excinfo.value.code == "TOOL_MCP_ERROR"
    assert excinfo.value.message == "remote mcp tool call failed"
    fields = {item["field"]: item["reason"] for item in excinfo.value.details}
    assert fields["mcp.tool_name"] == "search_documents"
    assert len(fields["mcp.remote_error"]) <= MCP_ERROR_SUMMARY_LIMIT + 3


async def test_connection_failure_is_reported_as_tool_mcp_error(tenant_env: TenantEnv) -> None:
    actor = await _actor(tenant_env)
    session = FakeMcpSession(error=ConnectionRefusedError("dial tcp 10.0.0.1:443 refused"))
    service, version_id = await _granted_tool(actor, spec=MCP_SPEC, session=session)

    with pytest.raises(ToolMcpError) as excinfo:
        await service.execute(actor=actor, tool_version_id=version_id, arguments={"query": "q"})

    assert excinfo.value.code == "TOOL_MCP_ERROR"
    assert "refused" not in excinfo.value.message


async def test_timeout_is_reported_as_tool_timeout(tenant_env: TenantEnv) -> None:
    actor = await _actor(tenant_env)
    session = FakeMcpSession(error=TimeoutError())
    service, version_id = await _granted_tool(actor, spec=MCP_SPEC, session=session)

    with pytest.raises(ToolTimeoutError) as excinfo:
        await service.execute(actor=actor, tool_version_id=version_id, arguments={"query": "q"})

    assert excinfo.value.http_status == 504


async def test_output_schema_violation_is_reported(tenant_env: TenantEnv) -> None:
    actor = await _actor(tenant_env)
    session = FakeMcpSession(
        result=McpCallResult(output={"hits": "three"}, remote_tool_name="search_documents")
    )
    service, version_id = await _granted_tool(actor, spec=MCP_SPEC, session=session)

    with pytest.raises(ToolOutputInvalidError) as excinfo:
        await service.execute(actor=actor, tool_version_id=version_id, arguments={"query": "q"})

    assert excinfo.value.code == "TOOL_OUTPUT_INVALID"


async def test_invalid_arguments_never_reach_remote_server(tenant_env: TenantEnv) -> None:
    actor = await _actor(tenant_env)
    session = FakeMcpSession(
        result=McpCallResult(output={"hits": 0}, remote_tool_name="search_documents")
    )
    service, version_id = await _granted_tool(actor, spec=MCP_SPEC, session=session)

    with pytest.raises(ValidationFailedError) as excinfo:
        await service.execute(actor=actor, tool_version_id=version_id, arguments={})

    assert session.calls == []
    assert excinfo.value.details[0]["field"].startswith("arguments")


async def test_secret_is_injected_into_mcp_headers(tenant_env: TenantEnv) -> None:
    actor = await _actor(tenant_env)
    secret = await secret_service.create(
        actor=actor, name=f"mcp-{uuid.uuid4().hex[:6]}", value="mcp-token"
    )
    session = FakeMcpSession(
        result=McpCallResult(output={"hits": 1}, remote_tool_name="search_documents")
    )
    service, version_id = await _granted_tool(
        actor,
        spec={
            **MCP_SPEC,
            "auth": {"type": "bearer", "secret_ref_id": str(secret.id)},
            "headers": {"X-Trace": "on"},
        },
        session=session,
    )

    await service.execute(actor=actor, tool_version_id=version_id, arguments={"query": "q"})

    assert session.calls[0]["headers"] == {"X-Trace": "on", "Authorization": "Bearer mcp-token"}


async def test_ungranted_mcp_tool_is_blocked(tenant_env: TenantEnv) -> None:
    actor = await _actor(tenant_env)
    session = FakeMcpSession(
        result=McpCallResult(output={"hits": 0}, remote_tool_name="search_documents")
    )
    service = ToolService(mcp_session=session)
    _tool, version = await service.create(
        actor=actor,
        code=f"mcp-nogrant-{uuid.uuid4().hex[:6]}",
        name="Not Granted",
        description=None,
        spec=MCP_SPEC,
        risk_level=RiskLevel.READ_ONLY,
        type="MCP",
    )

    with pytest.raises(ResourceUnavailableError):
        await service.execute(actor=actor, tool_version_id=version.id, arguments={"query": "q"})
    assert session.calls == []


async def test_high_risk_mcp_tool_requires_approval(tenant_env: TenantEnv) -> None:
    actor = await _actor(tenant_env)
    session = FakeMcpSession(
        result=McpCallResult(output={"hits": 1}, remote_tool_name="search_documents")
    )
    service, version_id = await _granted_tool(
        actor, spec=MCP_SPEC, session=session, risk_level=RiskLevel.HIGH
    )

    with pytest.raises(HighRiskToolError):
        await service.execute(actor=actor, tool_version_id=version_id, arguments={"query": "q"})
    assert session.calls == []

    result = await service.execute(
        actor=actor,
        tool_version_id=version_id,
        arguments={"query": "q"},
        allow_high_risk=True,
    )
    assert result.output == {"hits": 1}
