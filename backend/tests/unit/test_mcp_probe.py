"""MCP 探测：端点参数透传、结论归一、空清单不算失败、不触达调用路径。"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest

from agent_platform.contexts.catalog.application.mcp_probe_service import McpProbeService
from agent_platform.contexts.catalog.domain.enums import McpProbeVerdict
from agent_platform.contexts.catalog.infrastructure.mcp_client import McpToolDescriptor
from agent_platform.shared.context import ActorContext
from agent_platform.shared.errors import ValidationFailedError

TENANT_ID = uuid.uuid4()

ENDPOINT_SPEC: dict[str, Any] = {
    "transport": "streamable_http",
    "url": "https://knowledge.example.com/mcp",
    "timeout_s": 8.0,
    "headers": {"X-Trace": "on"},
    "auth": {"type": "none"},
}


def _actor() -> ActorContext:
    return ActorContext(tenant_id=TENANT_ID, actor_type="USER", actor_id=uuid.uuid4())


class _FakeSession:
    """替身 McpSession：记录探测传入的参数，并按需抛出预设异常。"""

    def __init__(
        self,
        *,
        tools: list[McpToolDescriptor] | None = None,
        error: BaseException | None = None,
    ) -> None:
        self._tools = tools or []
        self._error = error
        self.list_calls: list[dict[str, Any]] = []
        self.call_tool_calls: list[dict[str, Any]] = []

    async def call_tool(self, **kwargs: Any) -> Any:
        self.call_tool_calls.append(kwargs)
        raise AssertionError("探测不得触达工具调用路径")

    async def list_tools(
        self, *, url: str, transport: str, headers: dict[str, str], timeout_s: float
    ) -> list[McpToolDescriptor]:
        self.list_calls.append(
            {"url": url, "transport": transport, "headers": headers, "timeout_s": timeout_s}
        )
        if self._error is not None:
            raise self._error
        return list(self._tools)


def _status_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://knowledge.example.com/mcp")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError("remote rejected", request=request, response=response)


async def test_probe_passes_endpoint_settings_and_only_lists_tools() -> None:
    descriptor = McpToolDescriptor(
        name="search_documents",
        description="按关键词检索知识库文档。",
        input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
    )
    session = _FakeSession(tools=[descriptor])

    result = await McpProbeService(mcp_session=session).probe(
        actor=_actor(), spec=dict(ENDPOINT_SPEC)
    )

    assert result.verdict is McpProbeVerdict.OK
    assert result.tools == [descriptor]
    assert result.error_summary is None
    assert result.latency_ms >= 0
    assert session.list_calls == [
        {
            "url": "https://knowledge.example.com/mcp",
            "transport": "streamable_http",
            "headers": {"X-Trace": "on"},
            "timeout_s": 8.0,
        }
    ]
    # 探测只走发现路径：不调用远端工具，也就不会产生调用记录
    assert session.call_tool_calls == []


async def test_probe_treats_empty_tool_list_as_success() -> None:
    session = _FakeSession(tools=[])

    result = await McpProbeService(mcp_session=session).probe(
        actor=_actor(), spec=dict(ENDPOINT_SPEC)
    )

    assert result.verdict is McpProbeVerdict.OK
    assert result.tools == []
    assert result.error_summary is None


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (TimeoutError(), McpProbeVerdict.MCP_PROBE_TIMEOUT),
        (_status_error(401), McpProbeVerdict.MCP_PROBE_AUTH_FAILED),
        (_status_error(403), McpProbeVerdict.MCP_PROBE_AUTH_FAILED),
        (httpx.ConnectError("connection refused"), McpProbeVerdict.MCP_PROBE_UNREACHABLE),
        (RuntimeError("session closed"), McpProbeVerdict.MCP_PROBE_PROTOCOL_ERROR),
        (_status_error(500), McpProbeVerdict.MCP_PROBE_PROTOCOL_ERROR),
    ],
)
async def test_probe_maps_remote_failures_to_distinct_verdicts(
    error: BaseException, expected: McpProbeVerdict
) -> None:
    session = _FakeSession(error=error)

    result = await McpProbeService(mcp_session=session).probe(
        actor=_actor(), spec=dict(ENDPOINT_SPEC)
    )

    assert result.verdict is expected
    assert result.tools == []
    assert result.error_summary


async def test_probe_unwraps_exception_group_from_task_group() -> None:
    grouped = ExceptionGroup("transport failed", [TimeoutError()])
    session = _FakeSession(error=grouped)

    result = await McpProbeService(mcp_session=session).probe(
        actor=_actor(), spec=dict(ENDPOINT_SPEC)
    )

    assert result.verdict is McpProbeVerdict.MCP_PROBE_TIMEOUT


async def test_probe_rejects_incomplete_endpoint_before_connecting() -> None:
    session = _FakeSession()

    with pytest.raises(ValidationFailedError):
        await McpProbeService(mcp_session=session).probe(
            actor=_actor(), spec={"transport": "streamable_http", "timeout_s": 8.0}
        )

    assert session.list_calls == []
