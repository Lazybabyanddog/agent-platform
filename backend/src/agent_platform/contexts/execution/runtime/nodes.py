"""执行图节点：渲染入参、调用模型、执行工具，并统一消费预算。

节点只依赖端口协议，不感知厂商 SDK；预算耗尽统一抛 ``BudgetExceededError``，
由 ``executor`` 映射为运行记录的失败分类。
"""

from __future__ import annotations

import hashlib
import json
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
from agent_platform.contexts.execution.runtime.context import build_context_message, with_context
from agent_platform.contexts.execution.runtime.input_text import input_text
from agent_platform.shared.logging import get_logger

logger = get_logger(__name__)

STATE_MESSAGES = "messages"

Node = Callable[["RuntimeState"], Awaitable[dict[str, Any]]]


class RuntimeState(TypedDict):
    input: dict[str, Any]
    messages: list[dict[str, Any]]
    history: list[dict[str, Any]]
    used_steps: int
    used_tokens: int
    output: str
    # 本次执行的运行时上下文消息；只在调用模型时插入，不进 messages
    runtime_context: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class ToolRuntime:
    tool_id: str
    tool_code: str
    tool_version_id: str
    # 工具版本声明的业务终态：这些返回值不因换参数而改变
    terminal_statuses: tuple[str, ...] = ()


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


def _logged_node(name: str) -> Callable[[Node], Node]:
    """节点流转只放 DEBUG：基数高，生产默认不输出。"""

    def decorate(node: Node) -> Node:
        @wraps(node)
        async def wrapped(state: RuntimeState) -> dict[str, Any]:
            produced = await node(state)
            merged = {**state, **produced}
            logger.debug(
                "runtime.node",
                node=name,
                used_steps=merged.get("used_steps", 0),
                used_tokens=merged.get("used_tokens", 0),
                message_count=len(merged.get("messages") or []),
            )
            return produced

        return wrapped

    return decorate


def tool_runtimes(snapshot: dict[str, Any]) -> list[ToolRuntime]:
    return [
        ToolRuntime(
            tool_id=str(item["tool_id"]),
            tool_code=str(item["tool_code"]),
            tool_version_id=str(item["tool_version_id"]),
            terminal_statuses=_terminal_statuses(item.get("spec") or {}),
        )
        for item in snapshot.get("tools", [])
    ]


def _terminal_statuses(spec: dict[str, Any]) -> tuple[str, ...]:
    """读取工具版本声明的业务终态；未声明时按空集合处理。"""
    returns = spec.get("returns")
    if not isinstance(returns, dict):
        return ()
    raw = returns.get("terminal_statuses")
    if not isinstance(raw, list):
        return ()
    return tuple(str(item) for item in raw if isinstance(item, str) and item.strip())


def _arguments_fingerprint(arguments: dict[str, Any]) -> str:
    payload = json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _tool_message(call_id: str, content: str) -> dict[str, Any]:
    return {
        "role": "tool",
        "content": content,
        "tool_calls": [],
        "tool_call_id": call_id,
    }


def _error_envelope(code: str | None, message: str, details: tuple[dict[str, str], ...]) -> str:
    """失败统一信封：让模型不必猜"这是错误还是数据"。"""
    return json.dumps(
        {
            "status": "error",
            "recoverable": True,
            "code": code,
            "message": message,
            "fields": list(details),
            "hint": "修正参数后可以重新调用。",
        },
        ensure_ascii=False,
    )


def _terminal_envelope(status: str, message: str) -> str:
    return json.dumps(
        {
            "status": status,
            "recoverable": False,
            "code": None,
            "message": message,
            "fields": [],
            "hint": "这是业务终态，结果不会因更换参数而改变；请直接告知用户，不要重试。",
        },
        ensure_ascii=False,
    )


def _terminal_status(content: str, declared: tuple[str, ...]) -> tuple[str, str] | None:
    """工具成功返回但声明为终态时，取出状态与可读说明。"""
    if not declared:
        return None
    try:
        payload = json.loads(content)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    status = str(payload.get("status") or "")
    if status not in declared:
        return None
    message = payload.get("message")
    return status, str(message) if message else content


def model_tool_bindings(snapshot: dict[str, Any]) -> list[ToolBinding]:
    bindings: list[ToolBinding] = []
    for item in snapshot.get("tools", []):
        spec = item.get("spec") or {}
        bindings.append(
            ToolBinding(
                code=str(item["tool_code"]),
                description=str(spec.get("description") or "").strip() or None,
                # 模型看 model_schema（含参数说明与格式），平台按 input_schema 校验；
                # 历史版本没有 model_schema，回退用调用契约
                input_schema=spec.get("model_schema")
                or spec.get("input_schema")
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
    # 每次执行独立：记录已失败的工具+参数组合，用于短路原地打转
    attempted: set[tuple[str, str]] = set()

    @_logged_node("render")
    async def render_node(state: RuntimeState) -> dict[str, Any]:
        rendered = render_messages(prompt["messages"], prompt["variables"], state["input"])
        current = [{"role": item["role"], "content": item["content"]} for item in rendered]
        # 历史里的 system 是上一轮渲染出的提示词，本轮已重新渲染，去掉避免叠加
        history = [item for item in (state.get("history") or []) if item.get("role") != "system"]
        if current and current[0]["role"] == "system":
            messages = [current[0], *history, *current[1:]]
        else:
            messages = [*history, *current]
        # 模板未必写了用户消息（常见于单条 system Prompt）：把本次输入补进去，
        # 否则输入只落在 run.input，模型与历史都看不到。只看本轮模板，
        # 历史里的 user 是上一轮的，不能据此跳过本轮追加。
        if not any(item["role"] == "user" for item in current):
            text = input_text(state.get("input"), prompt.get("variables"))
            if text.strip():
                messages.append({"role": "user", "content": text})
        # 上下文在此取一次，同一 run 内所有模型调用复用，避免跨时间边界漂移
        context = build_context_message(snapshot.get("runtime_context"))
        return {"messages": messages, "history": [], "runtime_context": context}

    @_logged_node("llm")
    @step_budget(budget)
    async def llm_node(state: RuntimeState) -> dict[str, Any]:
        request = ModelRequest(
            model_name=str(snapshot["model"]["model_name"]),
            params=dict(snapshot["model"].get("params") or {}),
            messages=[
                _to_chat_message(item)
                for item in with_context(
                    state["messages"], state.get("runtime_context")
                )
            ],
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

    @_logged_node("tools")
    async def tool_node(state: RuntimeState) -> dict[str, Any]:
        last = state["messages"][-1]
        produced: list[dict[str, Any]] = []
        for raw_call in last.get("tool_calls") or []:
            runtime = runtimes.get(str(raw_call["name"]))
            if runtime is None:
                raise ToolNotAllowedError(str(raw_call["name"]))
            arguments = dict(raw_call.get("arguments") or {})
            call_id = str(raw_call["id"])
            fingerprint = (runtime.tool_code, _arguments_fingerprint(arguments))
            budget.consume_step()
            # 相同工具 + 相同参数已经失败过：再调一次只会得到同样结果，
            # 直接回灌终态，省下一次外部调用
            if fingerprint in attempted:
                produced.append(
                    _tool_message(
                        call_id,
                        _terminal_envelope(
                            "repeated_failure",
                            "这组参数刚刚调用过并失败，结果不会改变。",
                        ),
                    )
                )
                continue
            result = await tools.invoke(
                tool_code=runtime.tool_code,
                tool_version_id=runtime.tool_version_id,
                arguments=arguments,
            )
            if result.ok:
                terminal = _terminal_status(result.content, runtime.terminal_statuses)
                if terminal is None:
                    produced.append(_tool_message(call_id, result.content))
                else:
                    status, message = terminal
                    produced.append(_tool_message(call_id, _terminal_envelope(status, message)))
                continue
            attempted.add(fingerprint)
            if not result.recoverable:
                # 未授权、高风险、契约损坏：重试无意义，直接收敛
                raise ToolExecutionError(result.error_code or "TOOL_ERROR", result.content)
            produced.append(
                _tool_message(
                    call_id,
                    _error_envelope(result.error_code, result.content, result.details),
                )
            )
        return {"messages": [*state["messages"], *produced], "used_steps": budget.used_steps}

    def route(state: RuntimeState) -> str:
        last = state["messages"][-1] if state["messages"] else {}
        tool_calls = last.get("tool_calls") or []
        target = "tools" if tool_calls else "end"
        logger.debug("runtime.route", next=target, tool_calls=len(tool_calls))
        return target

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
