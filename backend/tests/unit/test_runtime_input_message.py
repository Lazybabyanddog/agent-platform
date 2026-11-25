"""运行时把本次输入物化为用户消息的契约测试。

模板只写 system 时输入原本没有落点：模型收不到、checkpoint 也不会有 user，
历史回放因此解析不出轮次。这里固定几种模板/输入形态，防止再次退化。
"""

from __future__ import annotations

import uuid
from typing import Any

from agent_platform.contexts.execution.domain.budget import ExecutionBudget
from agent_platform.contexts.execution.domain.ports import ModelRequest, ModelResponse, ToolResult
from agent_platform.contexts.execution.runtime.executor import execute_snapshot
from agent_platform.contexts.execution.runtime.nodes import build_nodes


def _snapshot(
    *,
    messages: list[dict[str, Any]],
    variables: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "snapshot_schema_version": 1,
        "model": {"model_id": str(uuid.uuid4()), "model_name": "mock", "params": {}},
        "prompt": {
            "prompt_id": str(uuid.uuid4()),
            "prompt_version_id": str(uuid.uuid4()),
            "version_no": 1,
            "messages": messages,
            "variables": variables,
        },
        "tools": [],
        "policy": {"max_steps": 3, "timeout_s": 5, "token_budget": 10000},
    }


class RecordingModel:
    def __init__(self, *, content: str = "done") -> None:
        self.content = content
        self.requests: list[ModelRequest] = []

    async def chat(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return ModelResponse(content=self.content, input_tokens=1, output_tokens=1)


class NoopTools:
    async def invoke(self, *, tool_code, tool_version_id, arguments) -> ToolResult:
        raise AssertionError("tools are not bound in these cases")


def _state(input_values: dict[str, Any], history: list[dict[str, Any]] | None = None) -> dict:
    return {
        "input": input_values,
        "messages": [],
        "history": list(history or []),
        "used_steps": 0,
        "used_tokens": 0,
        "output": "",
    }


async def test_system_only_prompt_appends_input_as_user_message() -> None:
    snapshot = _snapshot(
        messages=[{"role": "system", "content": "你是天气助手"}],
        variables=[{"name": "day", "type": "string", "required": True}],
    )
    model = RecordingModel()
    await execute_snapshot(
        snapshot=snapshot,
        model=model,
        tools=NoopTools(),
        input_values={"day": "帮我查下武汉明天天气"},
    )
    messages = model.requests[-1].messages
    assert [message.role for message in messages] == ["system", "user"]
    assert messages[-1].content == "帮我查下武汉明天天气"


async def test_template_user_message_is_not_duplicated() -> None:
    snapshot = _snapshot(
        messages=[{"role": "user", "content": "Q: {{question}}"}],
        variables=[{"name": "question", "type": "string", "required": True}],
    )
    model = RecordingModel()
    await execute_snapshot(
        snapshot=snapshot,
        model=model,
        tools=NoopTools(),
        input_values={"question": "退款流程"},
    )
    messages = model.requests[-1].messages
    assert [message.role for message in messages] == ["user"]
    assert messages[0].content == "Q: 退款流程"


async def test_multiple_variables_are_joined_in_declared_order() -> None:
    snapshot = _snapshot(
        messages=[{"role": "system", "content": "你是行程助手"}],
        variables=[
            {"name": "city", "type": "string", "required": True},
            {"name": "days", "type": "integer"},
        ],
    )
    model = RecordingModel()
    await execute_snapshot(
        snapshot=snapshot,
        model=model,
        tools=NoopTools(),
        input_values={"days": 3, "city": "武汉"},
    )
    user = model.requests[-1].messages[-1]
    assert user.role == "user"
    assert user.content == "city: 武汉\ndays: 3"


async def test_system_prompt_appears_once_after_two_renders() -> None:
    snapshot = _snapshot(
        messages=[{"role": "system", "content": "你是天气助手"}],
        variables=[{"name": "day", "type": "string", "required": True}],
    )
    nodes = build_nodes(
        snapshot=snapshot,
        model=RecordingModel(),
        tools=NoopTools(),
        budget=ExecutionBudget(max_steps=3, token_budget=10000, timeout_s=5),
    )

    first = await nodes.render(_state({"day": "第一问"}))
    assert [item["role"] for item in first["messages"]] == ["system", "user"]

    # 第二轮：上一轮的 messages 作为线程历史回放，system 被丢弃后重新渲染一次
    thread = [*first["messages"], {"role": "assistant", "content": "第一答"}]
    second = await nodes.render(_state({"day": "第二问"}, history=thread))
    messages = second["messages"]
    assert [item["role"] for item in messages].count("system") == 1
    assert messages[0]["content"] == "你是天气助手"
    assert [item["role"] for item in messages[1:]].count("user") == 2
    assert [item["role"] for item in messages[1:]].count("assistant") == 1
    assert messages[-1] == {"role": "user", "content": "第二问"}


async def test_no_session_identifier_does_not_load_thread_context() -> None:
    """没有会话标识时既不读历史、也不返回 checkpoint 引用。

    正式调用路径在没有会话标识时给每次运行独立的 thread，因此这里断言
    "本次执行不继承任何上下文"，上下文只存在于本次执行结果里。
    """
    snapshot = _snapshot(
        messages=[{"role": "system", "content": "你是天气助手"}],
        variables=[{"name": "day", "type": "string", "required": True}],
    )
    model = RecordingModel()
    for text in ("第一次提问", "第二次提问"):
        result = await execute_snapshot(
            snapshot=snapshot,
            model=model,
            tools=NoopTools(),
            input_values={"day": text},
            checkpointer=None,
            thread_id=None,
        )
        assert result.history_kept == 0
        assert result.history_trimmed == 0
        assert result.checkpoint_id is None
        messages = model.requests[-1].messages
        assert [message.role for message in messages] == ["system", "user"]
        assert messages[-1].content == text
