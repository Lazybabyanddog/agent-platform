"""ToolPort 适配层：MCP 失败只有错误码映射变化，执行流程不变。"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

from agent_platform.contexts.catalog.domain.enums import ToolType
from agent_platform.contexts.catalog.domain.errors import ToolMcpError
from agent_platform.contexts.execution.infrastructure.tool_adapter import CatalogToolPort

ACTOR = SimpleNamespace(tenant_id=uuid.uuid4())


class _FailingService:
    def __init__(self, error: Exception) -> None:
        self._error = error

    async def execute(self, **_: Any) -> Any:
        raise self._error


class _EchoService:
    async def execute(self, **_: Any) -> Any:
        return SimpleNamespace(status="SUCCESS", output={"hits": 2})


async def test_mcp_error_maps_to_tool_mcp_error_code() -> None:
    port = CatalogToolPort(
        actor=ACTOR, service=_FailingService(ToolMcpError(remote_tool_name="search"))
    )

    result = await port.invoke(
        tool_code="knowledge.search",
        tool_version_id=str(uuid.uuid4()),
        arguments={"query": "q"},
    )

    assert result.ok is False
    assert result.error_code == "TOOL_MCP_ERROR"
    assert result.content == "remote mcp tool call failed"


async def test_successful_output_is_json_encoded() -> None:
    port = CatalogToolPort(actor=ACTOR, service=_EchoService())

    result = await port.invoke(
        tool_code="knowledge.search",
        tool_version_id=str(uuid.uuid4()),
        arguments={"query": "q"},
    )

    assert result.ok is True
    assert result.content == '{"hits": 2}'


def test_mcp_is_a_generic_tool_type_value() -> None:
    assert ToolType.MCP.value == "MCP"
    assert ToolType.HTTP.value == "HTTP"
