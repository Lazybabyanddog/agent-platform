"""工具卡与失败回灌：模型能不能看到契约、能不能在一步内改对。

这里固定四件事：工具卡带描述与参数格式、可恢复失败回到模型、
不可恢复失败终止、同参数重复调用被短路。
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from agent_platform.contexts.execution.domain.ports import (
    ModelRequest,
    ModelResponse,
    ToolCall,
    ToolResult,
)
from agent_platform.contexts.execution.domain.enums import RunErrorCode
from agent_platform.contexts.execution.runtime.executor import (
    AgentRuntimeError,
    execute_snapshot,
)

WEATHER_SPEC: dict[str, Any] = {
    "description": "查询某城市某日天气；无数据表示该城市当日无记录，属终态",
    "input_schema": {
        "type": "object",
        "properties": {
            "city": {"type": "string"},
            "start_date": {"type": "string"},
        },
        "required": ["city", "start_date"],
    },
    "model_schema": {
        "type": "object",
        "properties": {
            "city": {"type": "string", "description": "中文城市名，如 武汉"},
            "start_date": {"type": "string", "description": "日期", "format": "date"},
        },
        "required": ["city", "start_date"],
    },
    "returns": {"terminal_statuses": ["no_data"]},
}


def _snapshot(spec: dict[str, Any] | None = None) -> dict[str, Any]:
    tool_id = str(uuid.uuid4())
    return {
        "snapshot_schema_version": 1,
        "model": {"model_id": str(uuid.uuid4()), "model_name": "mock", "params": {}},
        "prompt": {
            "prompt_id": str(uuid.uuid4()),
            "prompt_version_id": str(uuid.uuid4()),
            "version_no": 1,
            "messages": [{"role": "system", "content": "你是天气助手"}],
            "variables": [],
        },
        "tools": [
            {
                "tool_id": tool_id,
                "tool_code": "weather",
                "tool_version_id": str(uuid.uuid4()),
                "version_no": 1,
                "risk_level": "LOW",
                "spec": WEATHER_SPEC if spec is None else spec,
            }
        ],
        "policy": {"max_steps": 8, "timeout_s": 5, "token_budget": 10000},
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
        self.calls: list[dict[str, Any]] = []

    async def invoke(self, *, tool_code, tool_version_id, arguments) -> ToolResult:
        self.calls.append(arguments)
        return self._results.pop(0)


def _call(name: str = "weather", **arguments: Any) -> ModelResponse:
    return ModelResponse(
        content="",
        tool_calls=(ToolCall(id="call-1", name=name, arguments=arguments),),
        input_tokens=1,
        output_tokens=1,
    )


def _answer(text: str = "明天武汉多云") -> ModelResponse:
    return ModelResponse(content=text, input_tokens=1, output_tokens=1)


def _tool_payload(request: ModelRequest) -> dict[str, Any]:
    tool_messages = [item for item in request.messages if item.role == "tool"]
    return json.loads(tool_messages[-1].content)


async def test_tool_card_exposes_description_and_format() -> None:
    model = ScriptedModel([_answer()])

    await execute_snapshot(
        snapshot=_snapshot(),
        model=model,
        tools=StubTools(),
        input_values={},
    )

    binding = model.requests[0].tools[0]
    assert binding.description == WEATHER_SPEC["description"]
    # 模型看 model_schema：带参数说明与格式
    assert binding.input_schema["properties"]["start_date"]["format"] == "date"


async def test_tool_card_falls_back_to_input_schema() -> None:
    spec = {key: value for key, value in WEATHER_SPEC.items() if key != "model_schema"}
    model = ScriptedModel([_answer()])

    await execute_snapshot(
        snapshot=_snapshot(spec),
        model=model,
        tools=StubTools(),
        input_values={},
    )

    binding = model.requests[0].tools[0]
    assert binding.input_schema["properties"]["start_date"] == {"type": "string"}


async def test_recoverable_failure_returns_to_model() -> None:
    model = ScriptedModel([_call(city="武汉", start_date="明天"), _answer()])
    tools = StubTools(
        ToolResult(
            ok=False,
            content="request validation failed (arguments/start_date: '明天' is not a date)",
            error_code="INPUT_INVALID",
            details=({"field": "arguments/start_date", "reason": "'明天' is not a date"},),
            recoverable=True,
        )
    )

    result = await execute_snapshot(
        snapshot=_snapshot(), model=model, tools=tools, input_values={}
    )

    assert result.output == "明天武汉多云"
    envelope = _tool_payload(model.requests[1])
    assert envelope["status"] == "error"
    assert envelope["recoverable"] is True
    assert envelope["code"] == "INPUT_INVALID"
    assert envelope["fields"][0]["field"] == "arguments/start_date"


async def test_unrecoverable_failure_terminates_execution() -> None:
    model = ScriptedModel([_call(city="武汉", start_date="2026-11-25")])
    tools = StubTools(
        ToolResult(
            ok=False,
            content="tool requires approval",
            error_code="HIGH_RISK_BLOCKED",
            recoverable=False,
        )
    )

    with pytest.raises(AgentRuntimeError) as excinfo:
        await execute_snapshot(snapshot=_snapshot(), model=model, tools=tools, input_values={})

    assert excinfo.value.code is RunErrorCode.TOOL_ERROR
    assert excinfo.value.details["tool_error_code"] == "HIGH_RISK_BLOCKED"
    assert len(tools.calls) == 1
    # 终止后不再回到模型
    assert len(model.requests) == 1


async def test_terminal_result_is_reported_as_no_data() -> None:
    model = ScriptedModel(
        [_call(city="武汉", start_date="2026-11-25"), _answer("当天没有记录")]
    )
    tools = StubTools(
        ToolResult(ok=True, content=json.dumps({"status": "no_data", "message": "未找到天气数据"}))
    )

    await execute_snapshot(snapshot=_snapshot(), model=model, tools=tools, input_values={})

    envelope = _tool_payload(model.requests[1])
    assert envelope["status"] == "no_data"
    assert envelope["recoverable"] is False
    assert "不要重试" in envelope["hint"]


async def test_repeated_identical_call_is_short_circuited() -> None:
    model = ScriptedModel(
        [
            _call(city="武汉", start_date="2026-11-25"),
            _call(city="武汉", start_date="2026-11-25"),
            _answer("查不到"),
        ]
    )
    tools = StubTools(
        ToolResult(
            ok=False,
            content="boom",
            error_code="INPUT_INVALID",
            details=({"field": "arguments/start_date", "reason": "bad"},),
            recoverable=True,
        )
    )

    await execute_snapshot(snapshot=_snapshot(), model=model, tools=tools, input_values={})

    # 第二次相同参数没有再打到工具
    assert len(tools.calls) == 1
    envelope = _tool_payload(model.requests[2])
    assert envelope["status"] == "repeated_failure"
    assert envelope["recoverable"] is False
