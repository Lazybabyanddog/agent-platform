"""执行快照：预算、超时与失败分类都收敛在这里。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Protocol

from agent_platform.contexts.execution.domain.budget import BudgetExceededError, ExecutionBudget
from agent_platform.contexts.execution.domain.enums import RunErrorCode
from agent_platform.contexts.execution.domain.ports import ModelPort, ModelPortError, ToolPort
from agent_platform.contexts.execution.runtime.builder import build_graph
from agent_platform.contexts.execution.runtime.history import trim_history
from agent_platform.contexts.execution.runtime.nodes import (
    ToolExecutionError,
    ToolNotAllowedError,
)
from agent_platform.shared.errors import ValidationFailedError


class CheckpointTuple(Protocol):
    config: dict[str, Any]
    checkpoint: dict[str, Any]


class Checkpointer(Protocol):
    async def aget_tuple(self, config: dict[str, Any]) -> CheckpointTuple | None: ...


@dataclass(frozen=True, slots=True)
class RuntimeResult:
    output: str
    used_steps: int
    used_tokens: int
    checkpoint_id: str | None = None
    history_trimmed: int = 0
    history_kept: int = 0


class AgentRuntimeError(Exception):
    def __init__(
        self, code: RunErrorCode, message: str, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


async def execute_snapshot(
    *,
    snapshot: dict[str, Any],
    model: ModelPort,
    tools: ToolPort,
    input_values: dict[str, Any],
    timeout_s: float | None = None,
    checkpointer: Checkpointer | None = None,
    thread_id: str | None = None,
) -> RuntimeResult:
    policy = snapshot.get("policy") or {}
    budget = ExecutionBudget(
        max_steps=int(policy.get("max_steps") or 1),
        token_budget=int(policy.get("token_budget") or 1),
        timeout_s=float(policy.get("timeout_s") or 1),
    )
    graph = build_graph(
        snapshot=snapshot, model=model, tools=tools, budget=budget
    ).compile(checkpointer=checkpointer)
    config = {"configurable": {"thread_id": thread_id}} if thread_id else None
    effective_timeout = float(timeout_s or budget.timeout_s)

    history, trimmed = await _load_history(
        checkpointer=checkpointer,
        config=config,
        token_limit=int(policy.get("history_token_limit") or 4000),
    )

    try:
        async with asyncio.timeout(effective_timeout):
            state = await graph.ainvoke(
                {
                    "input": input_values,
                    "messages": [],
                    "history": history,
                    "used_steps": 0,
                    "used_tokens": 0,
                    "output": "",
                },
                config=config,
            )
    except TimeoutError as exc:
        raise AgentRuntimeError(
            RunErrorCode.TIMEOUT, "execution timed out", {"timeout_s": effective_timeout}
        ) from exc
    except BudgetExceededError as exc:
        raise AgentRuntimeError(
            RunErrorCode.BUDGET_EXCEEDED,
            str(exc),
            {"limit": exc.limit, "used_steps": budget.used_steps, "used_tokens": budget.used_tokens},
        ) from exc
    except ModelPortError as exc:
        raise AgentRuntimeError(
            RunErrorCode.MODEL_ERROR,
            "model call failed",
            {"model_error_code": exc.code.value},
        ) from exc
    except ToolExecutionError as exc:
        raise AgentRuntimeError(
            RunErrorCode.TOOL_ERROR, "tool execution failed", {"tool_error_code": exc.code}
        ) from exc
    except ToolNotAllowedError as exc:
        raise AgentRuntimeError(
            RunErrorCode.TOOL_ERROR,
            "model requested an unbound tool",
            {"tool_code": exc.tool_code},
        ) from exc
    except ValidationFailedError as exc:
        raise AgentRuntimeError(
            RunErrorCode.INPUT_INVALID, exc.message, {"details": exc.details}
        ) from exc

    checkpoint_id = None
    if checkpointer is not None and config is not None:
        checkpoint_tuple = await checkpointer.aget_tuple(config)
        if checkpoint_tuple is not None:
            checkpoint_id = (checkpoint_tuple.config.get("configurable") or {}).get(
                "checkpoint_id"
            )
    return RuntimeResult(
        output=str(state.get("output") or ""),
        used_steps=int(state.get("used_steps") or budget.used_steps),
        used_tokens=int(state.get("used_tokens") or budget.used_tokens),
        checkpoint_id=str(checkpoint_id) if checkpoint_id else None,
        history_trimmed=trimmed,
        history_kept=len(history),
    )


async def _load_history(
    *,
    checkpointer: Checkpointer | None,
    config: dict[str, Any] | None,
    token_limit: int,
) -> tuple[list[dict[str, Any]], int]:
    """同一 thread 上已有上下文时读取并裁剪，作为本轮的历史消息。"""
    if checkpointer is None or config is None:
        return [], 0
    try:
        found = await checkpointer.aget_tuple(config)
    except Exception:  # 历史读取失败不应阻断本轮执行
        return [], 0
    if found is None:
        return [], 0
    checkpoint = getattr(found, "checkpoint", None) or {}
    messages = (checkpoint.get("channel_values") or {}).get("messages") or []
    history, dropped = trim_history([dict(item) for item in messages], token_limit)
    return history, dropped


__all__ = ["AgentRuntimeError", "RuntimeResult", "execute_snapshot"]
