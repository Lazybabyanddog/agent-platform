"""执行节点与路由决策的 DEBUG 日志。"""

from __future__ import annotations

import importlib
import json
import uuid
from typing import Any

import pytest

from agent_platform.contexts.execution.domain.budget import ExecutionBudget
from agent_platform.contexts.execution.domain.ports import (
    ModelRequest,
    ModelResponse,
    ToolCall,
    ToolResult,
)
from agent_platform.settings import Settings
from agent_platform.shared.logging import configure_logging, get_logger, reset_logging


@pytest.fixture(autouse=True)
def _clean_logging():
    reset_logging()
    yield
    reset_logging()


def _records(captured: str) -> list[dict]:
    return [json.loads(line) for line in captured.splitlines() if line.startswith("{")]


class _FakeModel:
    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, _request: ModelRequest) -> ModelResponse:
        self.calls += 1
        if self.calls == 1:
            return ModelResponse(
                content="",
                tool_calls=(ToolCall(id="call-1", name="echo", arguments={}),),
                input_tokens=5,
                output_tokens=2,
            )
        return ModelResponse(content="done", input_tokens=1, output_tokens=1)


class _FakeTools:
    async def invoke(
        self, *, tool_code: str, tool_version_id: str, arguments: dict[str, Any]
    ) -> ToolResult:
        return ToolResult(ok=True, content="ok")


def _snapshot() -> dict[str, Any]:
    return {
        "prompt": {
            "messages": [{"role": "user", "content": "hello"}],
            "variables": [],
        },
        "model": {
            "model_id": str(uuid.uuid4()),
            "model_name": "fake-model",
            "params": {},
        },
        "tools": [
            {
                "tool_id": str(uuid.uuid4()),
                "tool_code": "echo",
                "tool_version_id": str(uuid.uuid4()),
                "spec": {"description": "echo", "input_schema": {"type": "object"}},
            }
        ],
    }


async def test_nodes_and_route_emit_events(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    configure_logging(Settings(log_level="DEBUG", log_json=True), service="api")
    nodes_module = importlib.import_module("agent_platform.contexts.execution.runtime.nodes")
    # 模块级 logger 可能在别的用例里已按 INFO 绑定，这里换一个按当前配置绑定的实例
    monkeypatch.setattr(nodes_module, "logger", get_logger("runtime"))

    nodes = nodes_module.build_nodes(
        snapshot=_snapshot(),
        model=_FakeModel(),
        tools=_FakeTools(),
        budget=ExecutionBudget(max_steps=5, token_budget=1000, timeout_s=30),
    )
    state: dict[str, Any] = {
        "input": {},
        "messages": [],
        "history": [],
        "used_steps": 0,
        "used_tokens": 0,
        "output": "",
    }

    state.update(await nodes.render(state))
    state.update(await nodes.llm(state))
    assert nodes.route(state) == "tools"
    state.update(await nodes.tools(state))
    state.update(await nodes.llm(state))
    assert nodes.route(state) == "end"

    records = [
        item for item in _records(capsys.readouterr().out) if item["event"].startswith("runtime.")
    ]
    sequence = [(item["event"], item.get("node") or item.get("next")) for item in records]
    assert sequence == [
        ("runtime.node", "render"),
        ("runtime.node", "llm"),
        ("runtime.route", "tools"),
        ("runtime.node", "tools"),
        ("runtime.node", "llm"),
        ("runtime.route", "end"),
    ]
    node_events = [item for item in records if item["event"] == "runtime.node"]
    assert all(item["service"] == "api" for item in node_events)
    assert node_events[-1]["used_steps"] == 3
    assert node_events[-1]["used_tokens"] == 9
