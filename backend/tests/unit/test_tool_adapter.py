"""ToolPort 适配层：MCP 失败只有错误码映射变化，执行流程不变。"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from agent_platform.contexts.catalog.domain.enums import ToolType
from agent_platform.contexts.catalog.domain.errors import (
    HighRiskToolError,
    ToolExecutionError,
    ToolMcpError,
    ToolOutputInvalidError,
    ToolTimeoutError,
)
from agent_platform.contexts.execution.infrastructure.tool_adapter import CatalogToolPort
from agent_platform.shared.errors import (
    NotFoundError,
    ResourceUnavailableError,
    ValidationFailedError,
)

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


@pytest.mark.parametrize(
    ("error", "code", "recoverable"),
    [
        (ValidationFailedError.for_field("start_date", "expected YYYY-MM-DD"), "INPUT_INVALID", True),
        (ToolTimeoutError(), "TOOL_TIMEOUT", True),
        (ToolExecutionError(), "TOOL_HTTP_ERROR", True),
        (ToolMcpError(remote_tool_name="search"), "TOOL_MCP_ERROR", True),
        (ResourceUnavailableError(), "RESOURCE_UNAVAILABLE", True),
        (NotFoundError("tool version missing"), "RESOURCE_UNAVAILABLE", True),
        (ToolOutputInvalidError(), "TOOL_OUTPUT_INVALID", False),
        (HighRiskToolError(), "HIGH_RISK_BLOCKED", False),
    ],
)
async def test_failure_maps_code_and_recoverability(
    error: Exception, code: str, recoverable: bool
) -> None:
    port = CatalogToolPort(actor=ACTOR, service=_FailingService(error))

    result = await port.invoke(
        tool_code="knowledge.search",
        tool_version_id=str(uuid.uuid4()),
        arguments={"query": "q"},
    )

    assert result.ok is False
    assert result.error_code == code
    assert result.recoverable is recoverable


async def test_validation_failure_puts_field_reason_into_message() -> None:
    port = CatalogToolPort(
        actor=ACTOR,
        service=_FailingService(
            ValidationFailedError.for_field("start_date", "expected YYYY-MM-DD")
        ),
    )

    result = await port.invoke(
        tool_code="weather",
        tool_version_id=str(uuid.uuid4()),
        arguments={"start_date": "11/25"},
    )

    assert result.content == (
        "request validation failed (start_date: expected YYYY-MM-DD)"
    )
    assert result.details == (
        {"field": "start_date", "reason": "expected YYYY-MM-DD"},
    )


async def test_argument_failure_message_carries_path_and_reason() -> None:
    reason = "'city' is a required property"
    port = CatalogToolPort(
        actor=ACTOR,
        service=_FailingService(
            ValidationFailedError(details=[{"field": "arguments.city", "reason": reason}])
        ),
    )

    result = await port.invoke(
        tool_code="weather",
        tool_version_id=str(uuid.uuid4()),
        arguments={"start_date": "2026-11-25"},
    )

    assert "arguments.city" in result.content
    assert reason in result.content
    assert result.details == ({"field": "arguments.city", "reason": reason},)


async def test_mcp_details_do_not_leak_into_message() -> None:
    port = CatalogToolPort(
        actor=ACTOR,
        service=_FailingService(
            ToolMcpError(remote_tool_name="search", remote_error="x" * 900)
        ),
    )

    result = await port.invoke(
        tool_code="knowledge.search",
        tool_version_id=str(uuid.uuid4()),
        arguments={"query": "q"},
    )

    assert result.content == "remote mcp tool call failed"
    fields = {item["field"]: item["reason"] for item in result.details}
    assert fields["mcp.tool_name"] == "search"
    assert len(fields["mcp.remote_error"]) == 503


def test_mcp_is_a_generic_tool_type_value() -> None:
    assert ToolType.MCP.value == "MCP"
    assert ToolType.HTTP.value == "HTTP"
