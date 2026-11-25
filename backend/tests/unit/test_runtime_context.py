"""运行时上下文：声明、渲染位置与取值稳定性。

上下文是平台事实，既不该由用户输入覆盖，也不该跨轮累积进会话历史。
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from agent_platform.contexts.agent.domain.snapshot import (
    build_snapshot,
    diff_snapshots,
    normalize_runtime_context,
)
from agent_platform.contexts.execution.domain.budget import ExecutionBudget
from agent_platform.contexts.execution.domain.ports import (
    ModelRequest,
    ModelResponse,
    ToolCall,
    ToolResult,
)
from agent_platform.contexts.execution.runtime.context import (
    build_context_message,
    with_context,
)
from agent_platform.contexts.execution.runtime.executor import execute_snapshot
from agent_platform.contexts.execution.runtime.nodes import build_nodes
from agent_platform.shared.errors import ValidationFailedError
from agent_platform.shared.hashing import config_hash

TOOL_SPEC: dict[str, Any] = {
    "description": "查询天气",
    "input_schema": {
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"],
    },
}

POLICY = {"max_steps": 6, "timeout_s": 5, "token_budget": 10000}


def _snapshot(*, runtime_context: list[str] | None = None, with_tool: bool = False) -> dict[str, Any]:
    tools = []
    if with_tool:
        tools.append(
            {
                "tool_id": str(uuid.uuid4()),
                "tool_code": "weather",
                "tool_version_id": str(uuid.uuid4()),
                "version_no": 1,
                "risk_level": "LOW",
                "spec": TOOL_SPEC,
            }
        )
    return {
        "snapshot_schema_version": 1,
        "model": {"model_id": str(uuid.uuid4()), "model_name": "mock", "params": {}},
        "prompt": {
            "prompt_id": str(uuid.uuid4()),
            "prompt_version_id": str(uuid.uuid4()),
            "version_no": 1,
            "messages": [{"role": "system", "content": "你是助手"}],
            "variables": [],
        },
        "tools": tools,
        "policy": dict(POLICY),
        "runtime_context": list(runtime_context or []),
    }


class ScriptedModel:
    def __init__(self, responses: list[ModelResponse]) -> None:
        self._responses = list(responses)
        self.requests: list[ModelRequest] = []

    async def chat(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return self._responses.pop(0)


class StubTools:
    def __init__(self, *results: ToolResult) -> None:
        self._results = list(results)

    async def invoke(self, *, tool_code, tool_version_id, arguments) -> ToolResult:
        del tool_code, tool_version_id, arguments
        return self._results.pop(0)


def _answer(text: str = "好的") -> ModelResponse:
    return ModelResponse(content=text, input_tokens=1, output_tokens=1)


def _call(**arguments: Any) -> ModelResponse:
    return ModelResponse(
        content="",
        tool_calls=(ToolCall(id="call-1", name="weather", arguments=arguments),),
        input_tokens=1,
        output_tokens=1,
    )


def test_declaration_accepts_whitelisted_items() -> None:
    assert normalize_runtime_context(["now", "timezone"]) == ["now", "timezone"]


def test_declaration_is_deduplicated_and_ordered() -> None:
    assert normalize_runtime_context(["timezone", "now", "now"]) == ["timezone", "now"]


def test_declaration_defaults_to_empty() -> None:
    assert normalize_runtime_context(None) == []
    assert normalize_runtime_context([]) == []


def test_unknown_declaration_is_rejected_with_item_name() -> None:
    with pytest.raises(ValidationFailedError) as excinfo:
        normalize_runtime_context(["now", "location"])

    detail = excinfo.value.details[0]
    assert detail["field"] == "runtime_context"
    assert "location" in detail["reason"]


def test_declaration_is_part_of_snapshot_and_diff() -> None:
    base = _snapshot()
    target = _snapshot(runtime_context=["now", "timezone"])

    assert base["runtime_context"] == []
    assert target["runtime_context"] == ["now", "timezone"]
    assert config_hash(base) != config_hash(target)

    compared = diff_snapshots(base, target)
    assert compared["identical"] is False
    assert compared["runtime_context"] == {
        "changed": True,
        "base": [],
        "target": ["now", "timezone"],
    }


def test_build_snapshot_keeps_declaration_only(monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot = build_snapshot(
        agent_code="agent-x",
        model={"model_id": "m"},
        prompt={"prompt_id": "p"},
        tools=[],
        policy=dict(POLICY),
        runtime_context=["now"],
    )

    assert snapshot["runtime_context"] == ["now"]
    # 只记录需要哪些事实，不把事实值冻结进快照
    assert "2026" not in json.dumps(snapshot, ensure_ascii=False)


def test_context_message_is_single_line_per_item(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_PLATFORM_TIMEZONE", "Asia/Shanghai")
    moment = datetime(2026, 11, 24, 9, 30, 5, tzinfo=timezone(timedelta(hours=8)))

    message = build_context_message(["now", "timezone"], now=moment)

    assert message is not None
    assert message["role"] == "system"
    assert message["content"] == (
        "当前时间：2026-11-24T09:30:05+08:00（周二）\n时区：Asia/Shanghai"
    )


def test_context_message_absent_when_undeclared() -> None:
    assert build_context_message([]) is None
    assert with_context([{"role": "system", "content": "s"}], None) == [
        {"role": "system", "content": "s"}
    ]


def test_context_is_inserted_after_system_and_before_history() -> None:
    message = {"role": "system", "content": "上下文"}
    messages = [
        {"role": "system", "content": "提示词"},
        {"role": "user", "content": "上一轮"},
        {"role": "assistant", "content": "上一轮回答"},
    ]

    assert with_context(messages, message) == [
        messages[0],
        message,
        messages[1],
        messages[2],
    ]


async def test_model_sees_context_after_system(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_PLATFORM_TIMEZONE", "Asia/Shanghai")
    model = ScriptedModel([_answer()])

    await execute_snapshot(
        snapshot=_snapshot(runtime_context=["now", "timezone"]),
        model=model,
        tools=StubTools(),
        input_values={},
    )

    request = model.requests[0]
    assert [item.role for item in request.messages] == ["system", "system"]
    assert "当前时间" in request.messages[1].content
    assert "时区" in request.messages[1].content


async def test_undeclared_version_has_no_context_message() -> None:
    model = ScriptedModel([_answer()])

    await execute_snapshot(
        snapshot=_snapshot(),
        model=model,
        tools=StubTools(),
        input_values={},
    )

    assert [item.role for item in model.requests[0].messages] == ["system"]


async def test_context_goes_between_system_and_history() -> None:
    model = ScriptedModel([_answer()])
    nodes = build_nodes(
        snapshot=_snapshot(runtime_context=["now"]),
        model=model,
        tools=StubTools(),
        budget=ExecutionBudget(max_steps=4, token_budget=1000, timeout_s=5),
    )
    state = {
        "input": {},
        "messages": [],
        "history": [
            {"role": "user", "content": "上一轮"},
            {"role": "assistant", "content": "上一轮回答"},
        ],
        "used_steps": 0,
        "used_tokens": 0,
        "output": "",
        "runtime_context": None,
    }

    rendered = await nodes.render(state)
    await nodes.llm({**state, **rendered})

    roles = [item.role for item in model.requests[0].messages]
    assert roles == ["system", "system", "user", "assistant"]
    assert "当前时间" in model.requests[0].messages[1].content
    assert model.requests[0].messages[2].content == "上一轮"


async def test_context_is_sampled_once_per_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_PLATFORM_TIMEZONE", "Asia/Shanghai")
    calls: list[int] = []
    original = build_context_message

    def counting(items, *, now=None):
        calls.append(1)
        moment = datetime(2026, 11, 24, 23, 59, 59, tzinfo=timezone(timedelta(hours=8)))
        moment = moment + timedelta(seconds=len(calls))
        return original(items, now=moment)

    monkeypatch.setattr(
        "agent_platform.contexts.execution.runtime.nodes.build_context_message", counting
    )

    model = ScriptedModel([_call(city="武汉"), _answer()])

    await execute_snapshot(
        snapshot=_snapshot(runtime_context=["now"], with_tool=True),
        model=model,
        tools=StubTools(ToolResult(ok=True, content=json.dumps({"ok": True}))),
        input_values={},
    )

    assert len(calls) == 1
    first, second = (request.messages[1].content for request in model.requests)
    assert first == second


async def test_input_cannot_override_context(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_PLATFORM_TIMEZONE", "Asia/Shanghai")
    snapshot = _snapshot(runtime_context=["now", "timezone"])
    # 调用方即便声明了同名变量，也只影响模板，不改变平台事实
    snapshot["prompt"]["variables"] = [
        {"name": "now", "type": "string", "required": False},
        {"name": "timezone", "type": "string", "required": False},
    ]
    model = ScriptedModel([_answer()])

    await execute_snapshot(
        snapshot=snapshot,
        model=model,
        tools=StubTools(),
        input_values={
            "now": "1999-01-01T00:00:00+00:00",
            "timezone": "Mars/Olympus",
        },
    )

    messages = model.requests[0].messages
    context_text = messages[1].content
    assert "1999" not in context_text
    assert "Mars/Olympus" not in context_text
    assert "Asia/Shanghai" in context_text
    # 伪造值只可能作为本轮输入出现，不进入上下文消息
    assert "1999" in messages[-1].content
