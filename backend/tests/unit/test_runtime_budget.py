"""执行图：步骤、token 与总超时预算。"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from agent_platform.contexts.execution.domain.enums import RunErrorCode
from agent_platform.contexts.execution.domain.ports import (
    ModelRequest,
    ModelResponse,
    ToolCall,
    ToolResult,
)
from agent_platform.contexts.execution.runtime.executor import (
    AgentRuntimeError,
    execute_snapshot,
)


def _snapshot(*, max_steps: int = 8, token_budget: int = 10000, timeout_s: float = 5) -> dict:
    tool_id = str(uuid.uuid4())
    return {
        "snapshot_schema_version": 1,
        "model": {"model_id": str(uuid.uuid4()), "model_name": "mock", "params": {}},
        "prompt": {
            "prompt_id": str(uuid.uuid4()),
            "prompt_version_id": str(uuid.uuid4()),
            "version_no": 1,
            "messages": [{"role": "user", "content": "{{question}}"}],
            "variables": [{"name": "question", "type": "string", "required": True}],
        },
        "tools": [
            {
                "tool_id": tool_id,
                "tool_code": "echo",
                "tool_version_id": str(uuid.uuid4()),
                "description": "echo",
                "input_schema": {"type": "object", "properties": {}},
            }
        ],
        "policy": {
            "max_steps": max_steps,
            "timeout_s": timeout_s,
            "token_budget": token_budget,
        },
    }


class LoopingModel:
    async def chat(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(
            content="",
            tool_calls=(ToolCall(id="call-1", name="echo", arguments={}),),
            input_tokens=1,
            output_tokens=1,
        )


class FinalModel:
    def __init__(self, *, tokens: int = 1, delay: float = 0) -> None:
        self.tokens = tokens
        self.delay = delay

    async def chat(self, request: ModelRequest) -> ModelResponse:
        if self.delay:
            await asyncio.sleep(self.delay)
        return ModelResponse(
            content="done", input_tokens=self.tokens, output_tokens=0
        )


class EchoTool:
    async def invoke(self, *, tool_code, tool_version_id, arguments) -> ToolResult:
        return ToolResult(ok=True, content="ok")


async def test_step_limit_stops_tool_loop() -> None:
    with pytest.raises(AgentRuntimeError) as caught:
        await execute_snapshot(
            snapshot=_snapshot(max_steps=2),
            model=LoopingModel(),
            tools=EchoTool(),
            input_values={"question": "hi"},
        )
    assert caught.value.code == RunErrorCode.BUDGET_EXCEEDED
    assert caught.value.details["limit"] == "STEP_LIMIT"


async def test_token_budget_stops_execution() -> None:
    with pytest.raises(AgentRuntimeError) as caught:
        await execute_snapshot(
            snapshot=_snapshot(token_budget=5),
            model=FinalModel(tokens=10),
            tools=EchoTool(),
            input_values={"question": "hi"},
        )
    assert caught.value.code == RunErrorCode.BUDGET_EXCEEDED
    assert caught.value.details["limit"] == "TOKEN_BUDGET"


async def test_total_timeout_is_enforced() -> None:
    with pytest.raises(AgentRuntimeError) as caught:
        await execute_snapshot(
            snapshot=_snapshot(timeout_s=5),
            model=FinalModel(delay=0.3),
            tools=EchoTool(),
            input_values={"question": "hi"},
            timeout_s=0.05,
        )
    assert caught.value.code == RunErrorCode.TIMEOUT


async def test_success_returns_output_and_usage() -> None:
    result = await execute_snapshot(
        snapshot=_snapshot(),
        model=FinalModel(tokens=7),
        tools=EchoTool(),
        input_values={"question": "hi"},
    )
    assert result.output == "done"
    assert result.used_steps == 1
    assert result.used_tokens == 7
    assert result.checkpoint_id is None
