"""MCP 工具发现：传输参数透传、超时传播、远端条目归一为平台结构。"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agent_platform.contexts.catalog.infrastructure import mcp_client
from agent_platform.contexts.catalog.infrastructure.mcp_client import SdkMcpSession


class _FakeTool:
    """替身：只保留 SDK 工具模型里探测用得到的三个字段。"""

    def __init__(
        self,
        name: str,
        description: str | None = None,
        input_schema: dict[str, Any] | None = None,
    ) -> None:
        self.name = name
        self.description = description
        self.inputSchema = input_schema


class _FakeListing:
    def __init__(self, tools: list[_FakeTool]) -> None:
        self.tools = tools


class _FakeClientSession:
    """替身 ClientSession：跳过真实握手，直接回预设的工具清单。"""

    def __init__(self, read_stream: Any, write_stream: Any, tools: list[_FakeTool]) -> None:
        self._tools = tools
        self.initialized = False

    async def __aenter__(self) -> _FakeClientSession:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def initialize(self) -> None:
        self.initialized = True

    async def list_tools(self) -> _FakeListing:
        return _FakeListing(self._tools)


class _FakeTransport:
    """替身传输：可选地先睡一会儿，用来验证超时预算真的生效。"""

    def __init__(self, *, delay_s: float = 0.0) -> None:
        self._delay_s = delay_s

    async def __aenter__(self) -> tuple[object, object]:
        if self._delay_s:
            await asyncio.sleep(self._delay_s)
        return (object(), object())

    async def __aexit__(self, *exc: object) -> bool:
        return False


def _patch(
    monkeypatch: pytest.MonkeyPatch,
    *,
    recorder: dict[str, Any],
    tools: list[_FakeTool],
    delay_s: float = 0.0,
) -> None:
    def fake_open_transport(
        *, url: str, transport: str, headers: dict[str, str], timeout_s: float
    ) -> _FakeTransport:
        recorder.update(
            {"url": url, "transport": transport, "headers": headers, "timeout_s": timeout_s}
        )
        return _FakeTransport(delay_s=delay_s)

    monkeypatch.setattr(mcp_client, "_open_transport", fake_open_transport)
    monkeypatch.setattr(
        mcp_client,
        "ClientSession",
        lambda read_stream, write_stream: _FakeClientSession(read_stream, write_stream, tools),
    )


async def test_list_tools_passes_transport_settings_and_normalizes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder: dict[str, Any] = {}
    _patch(
        monkeypatch,
        recorder=recorder,
        tools=[
            _FakeTool(
                "search_documents",
                "按关键词检索知识库文档。",
                {"type": "object", "properties": {"query": {"type": "string"}}},
            ),
            _FakeTool("no_contract"),
        ],
    )

    tools = await SdkMcpSession().list_tools(
        url="https://knowledge.example.com/mcp",
        transport="streamable_http",
        headers={"X-Trace": "on"},
        timeout_s=5.0,
    )

    assert recorder == {
        "url": "https://knowledge.example.com/mcp",
        "transport": "streamable_http",
        "headers": {"X-Trace": "on"},
        "timeout_s": 5.0,
    }
    assert [(t.name, t.description, t.input_schema) for t in tools] == [
        (
            "search_documents",
            "按关键词检索知识库文档。",
            {"type": "object", "properties": {"query": {"type": "string"}}},
        ),
        ("no_contract", None, None),
    ]


async def test_list_tools_returns_empty_list_when_remote_declares_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch(monkeypatch, recorder={}, tools=[])

    tools = await SdkMcpSession().list_tools(
        url="https://knowledge.example.com/mcp",
        transport="streamable_http",
        headers={},
        timeout_s=5.0,
    )

    assert tools == []


async def test_list_tools_honours_timeout_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, recorder={}, tools=[], delay_s=0.5)

    with pytest.raises(TimeoutError):
        await SdkMcpSession().list_tools(
            url="https://knowledge.example.com/mcp",
            transport="streamable_http",
            headers={},
            timeout_s=0.05,
        )


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://127.0.0.1:8002/mcp", True),
        ("http://localhost:8002/mcp", True),
        ("http://[::1]:8002/mcp", True),
        ("http://192.168.1.10:8002/mcp", True),
        ("http://10.0.0.5:8002/mcp", True),
        ("https://mcp.example.com/mcp", False),
    ],
)
def test_local_endpoint_detection(url: str, expected: bool) -> None:
    assert mcp_client._is_local_endpoint(url) is expected


def test_local_endpoint_ignores_system_proxy() -> None:
    """本机地址不受系统代理影响：否则本地 MCP 会被代理拦截（502）。

    外网端点仍保留 trust_env，让需要代理的部署照常工作。
    """
    local = mcp_client._http_client_factory("http://127.0.0.1:8002/mcp")()
    remote = mcp_client._http_client_factory("https://mcp.example.com/mcp")()

    assert local.trust_env is False
    assert remote.trust_env is True

