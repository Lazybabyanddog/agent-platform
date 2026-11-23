"""按版本快照组装 LangGraph 执行图：渲染 → LLM ⇄ 工具。"""

from __future__ import annotations

from typing import Any, Protocol

from langgraph.graph import END, StateGraph

from agent_platform.contexts.execution.domain.budget import ExecutionBudget
from agent_platform.contexts.execution.domain.ports import ModelPort, ToolPort
from agent_platform.contexts.execution.runtime.nodes import (
    RuntimeState,
    ToolExecutionError,
    ToolNotAllowedError,
    build_nodes,
    model_tool_bindings,
    tool_runtimes,
)


class CompiledGraph(Protocol):
    async def ainvoke(
        self, state: RuntimeState, config: dict[str, Any] | None = None
    ) -> RuntimeState: ...


def build_graph(
    *,
    snapshot: dict[str, Any],
    model: ModelPort,
    tools: ToolPort,
    budget: ExecutionBudget,
):
    nodes = build_nodes(snapshot=snapshot, model=model, tools=tools, budget=budget)

    graph = StateGraph(RuntimeState)
    graph.add_node("render", nodes.render)
    graph.add_node("llm", nodes.llm)
    graph.add_node("tools", nodes.tools)
    graph.set_entry_point("render")
    graph.add_edge("render", "llm")
    graph.add_conditional_edges("llm", nodes.route, {"tools": "tools", "end": END})
    graph.add_edge("tools", "llm")
    return graph


__all__ = [
    "CompiledGraph",
    "ToolExecutionError",
    "ToolNotAllowedError",
    "build_graph",
    "model_tool_bindings",
    "tool_runtimes",
]
