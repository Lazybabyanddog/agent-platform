"""MCP 调用与探测日志：拆开建连/调用耗时，URL 只出 host 与 path。"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest

from agent_platform.contexts.catalog.infrastructure import mcp_client as mcp_module
from agent_platform.contexts.catalog.infrastructure.mcp_client import SdkMcpSession
from agent_platform.settings import Settings
from agent_platform.shared.logging import configure_logging, reset_logging

SECRET_QUERY = "token=SECRET-VALUE"


@pytest.fixture(autouse=True)
def _clean_logging():
    reset_logging()
    configure_logging(Settings(log_level="INFO", log_json=True), service="api")
    yield
    reset_logging()


def _records(captured: str) -> list[dict]:
    return [json.loads(line) for line in captured.splitlines() if line.startswith("{")]


class _FakeSession:
    def __init__(
        self,
        *,
        result: Any = None,
        tools: list[Any] | None = None,
        error: Exception | None = None,
    ) -> None:
        self._result = result
        self._tools = tools or []
        self._error = error

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *_args: Any) -> bool:
        return False

    async def initialize(self) -> None:
        return None

    async def call_tool(self, _name: str, _arguments: dict[str, Any]) -> Any:
        if self._error is not None:
            raise self._error
        return self._result

    async def list_tools(self) -> Any:
        return SimpleNamespace(tools=self._tools)


def _patch_session(monkeypatch: pytest.MonkeyPatch, session: _FakeSession) -> None:
    @asynccontextmanager
    async def fake_transport(**_kwargs: Any):
        yield (object(), object())

    monkeypatch.setattr(mcp_module, "_open_transport", lambda **_kwargs: fake_transport())
    monkeypatch.setattr(mcp_module, "ClientSession", lambda *_args: session)


async def test_call_tool_logs_split_timings(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    session = _FakeSession(
        result=SimpleNamespace(
            content=[SimpleNamespace(type="text", text='{"echo": 1}')], isError=False
        )
    )
    _patch_session(monkeypatch, session)

    result = await SdkMcpSession().call_tool(
        url=f"https://mcp.example.com/sse?{SECRET_QUERY}",
        transport="sse",
        tool_name="echo",
        arguments={"q": 1},
        headers={"Authorization": "Bearer SECRET"},
        timeout_s=1,
    )

    assert result.is_error is False
    captured = capsys.readouterr().out
    record = _records(captured)[0]
    assert record["event"] == "mcp.call"
    assert record["url_host"] == "mcp.example.com"
    assert record["url_path"] == "/sse"
    assert record["transport"] == "sse"
    assert record["tool_name"] == "echo"
    assert isinstance(record["connect_ms"], int)
    assert isinstance(record["call_ms"], int)
    assert record["is_error"] is False
    assert "SECRET" not in captured


async def test_call_tool_failure_marks_stage(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_session(monkeypatch, _FakeSession(error=RuntimeError("boom")))

    with pytest.raises(RuntimeError):
        await SdkMcpSession().call_tool(
            url="https://mcp.example.com/mcp",
            transport="streamable_http",
            tool_name="echo",
            arguments={},
            headers={},
            timeout_s=1,
        )

    record = _records(capsys.readouterr().out)[0]
    assert record["event"] == "mcp.call"
    assert record["is_error"] is True
    assert record["failed_stage"] == "call"
    assert record["call_ms"] == 0
    assert isinstance(record["connect_ms"], int)


async def test_probe_logs_tool_count(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_session(
        monkeypatch,
        _FakeSession(
            tools=[
                SimpleNamespace(name="echo", description="e", inputSchema={}),
                SimpleNamespace(name="add", description=None, inputSchema=None),
            ]
        ),
    )

    tools = await SdkMcpSession().list_tools(
        url="https://mcp.example.com/mcp",
        transport="streamable_http",
        headers={},
        timeout_s=1,
    )

    assert [item.name for item in tools] == ["echo", "add"]
    record = _records(capsys.readouterr().out)[0]
    assert record["event"] == "mcp.probe"
    assert record["tools_count"] == 2
    assert record["ok"] is True
    assert record["url_path"] == "/mcp"
