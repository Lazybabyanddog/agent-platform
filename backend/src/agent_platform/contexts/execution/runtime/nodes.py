"""执行图节点：渲染入参、调用模型、执行工具，并统一消费预算。

节点只依赖端口协议，不感知厂商 SDK；预算耗尽统一抛 ``BudgetExceededError``，
由 ``executor`` 映射为运行记录的失败分类。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import wraps
from typing import Any, TypedDict

from agent_platform.contexts.catalog.domain.render import render_messages
from agent_platform.contexts.execution.domain.budget import ExecutionBudget
from agent_platform.contexts.execution.domain.ports import (
    ChatMessage,
    ModelPort,
    ModelRequest,
    ToolBinding,
    ToolCall,
    ToolPort,
)

STATE_MESSAGES = "messages"

Node = Callable[["RuntimeState"], Awaitable[dict[str, Any]]]


class RuntimeState(TypedDict):
    input: dict[str, Any]
    messages: list[dict[str, Any]]
    history: list[dict[str, Any]]
    used_steps: int
    used_tokens: int
    output: str


@dataclass(frozen=True, slots=True)
class ToolRuntime:
    tool_id: str
    tool_code: str
    tool_version_id: str


@dataclass(frozen=True, slots=True)
class Nodes:
    render: Node
    llm: Node
    tools: Node
    route: Callable[[RuntimeState], str]


def step_budget(budget: ExecutionBudget) -> Callable[[Node], Node]:
    """每次进入节点前扣一步；耗尽时在调用外部能力之前中止。"""

    def decorate(node: Node) -> Node:
        @wraps(node)
        async def wrapped(state: RuntimeState) -> dict[str, Any]:
            budget.consume_step()
            return await node(state)

        return wrapped

    return decorate


def tool_runtimes(snapshot: dict[str, Any]) -> list[ToolRuntime]:
    return [
        ToolRuntime(
            tool_id=str(item["tool_id"]),
            tool_code=str(item["tool_code"]),
            tool_version_id=str(item["tool_version_id"]),
        )
        for item in snapshot.get("tools", [])
    ]


def model_tool_bindings(snapshot: dict[str, Any]) -> list[ToolBinding]:
    bindings: list[ToolBinding] = []
    for item in snapshot.get("tools", []):
        spec = item.get("spec") or {}
        bindings.append(
            ToolBinding(
                code=str(item["tool_code"]),
                description=spec.get("description"),
                input_schema=spec.get("input_schema")
                or {"type": "object", "properties": {}},
            )
        )
    return bindings


def _to_chat_message(raw: dict[str, Any]) -> ChatMessage:
    return ChatMessage(
        role=str(raw["role"]),
        content=str(raw.get("content") or ""),
        tool_calls=tuple(
            ToolCall(
                id=str(call["id"]),
                name=str(call["name"]),
                arguments=dict(call.get("arguments") or {}),
            )
            for call in raw.get("tool_calls") or []
        ),
        tool_call_id=raw.get("tool_call_id"),
    )


def _from_chat_message(message: ChatMessage) -> dict[str, Any]:
    return {
        "role": message.role,
        "content": message.content,
        "tool_calls": [
            {"id": call.id, "name": call.name, "arguments": call.arguments}
            for call in message.tool_calls
        ],
        "tool_call_id": message.tool_call_id,
    }


def build_nodes(
    *,
    snapshot: dict[str, Any],
    model: ModelPort,
    tools: ToolPort,
    budget: ExecutionBudget,
) -> Nodes:
    prompt = snapshot["prompt"]
    bindings = model_tool_bindings(snapshot)
    runtimes = {item.tool_code: item for item in tool_runtimes(snapshot)}

    async def render_node(state: RuntimeState) -> dict[str, Any]:
        rendered = render_messages(prompt["messages"], prompt["variables"], state["input"])
        current = [{"role": item["role"], "content": item["content"]} for item in rendered]
        # 历史里的 system 是上一轮渲染出的提示词，本轮已重新渲染，去掉避免叠加
        history = [item for item in (state.get("history") or []) if item.get("role") != "system"]
        if current and current[0]["role"] == "system":
            messages = [current[0], *history, *current[1:]]
        else:
            messages = [*history, *current]
        return {"messages": messages, "history": []}

    @step_budget(budget)
    async def llm_node(state: RuntimeState) -> dict[str, Any]:
        request = ModelRequest(
            model_name=str(snapshot["model"]["model_name"]),
            params=dict(snapshot["model"].get("params") or {}),
            messages=[_to_chat_message(item) for item in state["messages"]],
            tools=bindings,
        )
        response = await model.chat(request)
        budget.consume_tokens(response.input_tokens + response.output_tokens)
        return {
            "messages": [
                *state["messages"],
                _from_chat_message(
                    ChatMessage(
                        role="assistant",
                        content=response.content,
                        tool_calls=response.tool_calls,
                    )
                ),
            ],
            "used_steps": budget.used_steps,
            "used_tokens": budget.used_tokens,
            "output": response.content,
        }

    async def tool_node(state: RuntimeState) -> dict[str, Any]:
        last = state["messages"][-1]
        produced: list[dict[str, Any]] = []
        for raw_call in last.get("tool_calls") or []:
            runtime = runtimes.get(str(raw_call["name"]))
            if runtime is None:
                raise ToolNotAllowedError(str(raw_call["name"]))
            budget.consume_step()
            result = await tools.invoke(
                tool_code=runtime.tool_code,
                tool_version_id=runtime.tool_version_id,
                arguments=dict(raw_call.get("arguments") or {}),
            )
            if not result.ok:
                raise ToolExecutionError(result.error_code or "TOOL_ERROR", result.content)
            produced.append(
                {
                    "role": "tool",
                    "content": result.content,
                    "tool_calls": [],
                    "tool_call_id": str(raw_call["id"]),
                }
            )
        return {"messages": [*state["messages"], *produced], "used_steps": budget.used_steps}

    def route(state: RuntimeState) -> str:
        last = state["messages"][-1] if state["messages"] else {}
        return "tools" if last.get("tool_calls") else "end"

    return Nodes(render=render_node, llm=llm_node, tools=tool_node, route=route)


class ToolNotAllowedError(Exception):
    """模型请求了快照之外的工具。"""

    def __init__(self, tool_code: str) -> None:
        super().__init__(f"tool {tool_code!r} is not part of this version")
        self.tool_code = tool_code


class ToolExecutionError(Exception):
    """工具执行失败：参数不合法、授权撤销或外部调用失败。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


__all__ = [
    "Nodes",
    "RuntimeState",
    "ToolExecutionError",
    "ToolNotAllowedError",
    "ToolRuntime",
    "build_nodes",
    "model_tool_bindings",
    "step_budget",
    "tool_runtimes",
]
