"""会话历史读取：把会话线程的消息原语还原成结构化轮次，供调试台回放。

真相源与会话执行一致，都是该会话 thread 上的 checkpoint；本模块只读，
不改动 checkpoint、不产生运行、不移动发布指针。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from agent_platform.contexts.execution.domain.enums import RunStatus
from agent_platform.contexts.execution.infrastructure.checkpoint_store import checkpoint_store
from agent_platform.contexts.execution.infrastructure.repositories import (
    ExecutionSessionRepository,
    RunRepository,
)
from agent_platform.contexts.execution.infrastructure.tables import ExecutionSession, Run
from agent_platform.contexts.execution.runtime.input_text import input_text
from agent_platform.contexts.insight.infrastructure.repositories import ToolCallRepository
from agent_platform.contexts.insight.infrastructure.tables import ToolCall
from agent_platform.shared.context import ActorContext
from agent_platform.shared.db import session_scope
from agent_platform.shared.errors import NotFoundError

DEFAULT_TURNS_LIMIT = 50
MAX_TURNS_LIMIT = 200
TOOL_STATUS_UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class HistoryMessage:
    """回放消息：正文原样返回，长度一并给出便于前端占位。"""

    content: str | None
    length: int


@dataclass(frozen=True, slots=True)
class HistoryTool:
    name: str
    status: str
    latency_ms: int | None
    error: str | None


@dataclass(frozen=True, slots=True)
class HistoryTurn:
    index: int
    user: HistoryMessage
    assistant: HistoryMessage | None
    tools: list[HistoryTool]
    run_id: uuid.UUID | None = None
    trace_id: str | None = None
    status: str | None = None
    error: dict[str, Any] | None = None
    started_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class SessionHistory:
    session: ExecutionSession
    turns: list[HistoryTurn]
    truncated: bool = False
    reconstructed: bool = False


@dataclass(slots=True)
class _RawTurn:
    """一轮的原始片段，字段取自 checkpoint 的消息原语。"""

    user: dict[str, Any] = field(default_factory=dict)
    answers: list[str] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)
    tool_messages: list[dict[str, Any]] = field(default_factory=list)


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for block in value:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "".join(parts)
    return ""


def _split_turns(messages: list[dict[str, Any]]) -> list[_RawTurn]:
    """按 human 消息切段；system 每轮重渲染，不作为历史内容返回。"""
    turns: list[_RawTurn] = []
    current: _RawTurn | None = None
    for raw in messages:
        message = raw if isinstance(raw, dict) else dict(raw)
        role = str(message.get("role") or message.get("type") or "")
        if role == "system":
            continue
        if role == "user":
            current = _RawTurn(user=message)
            turns.append(current)
            continue
        if current is None:
            continue
        if role == "assistant":
            calls = [
                dict(call) for call in (message.get("tool_calls") or []) if isinstance(call, dict)
            ]
            if calls:
                current.calls.extend(calls)
            else:
                text = _text(message.get("content"))
                if text.strip():
                    current.answers.append(text)
            continue
        if role == "tool":
            current.tool_messages.append(message)
    return turns


def _turns_from_runs(runs: list[Run], messages: list[dict[str, Any]]) -> list[_RawTurn]:
    """兜底重建：线程里没有 user 锚点时，以运行记录为轮次边界。

    回答只能从线程里按顺序取「无 tool_calls 的 assistant 消息」，
    并且只对成功的运行消费；取不到就留空，不猜测。
    """
    answers = [
        _text(message.get("content"))
        for message in messages
        if str(message.get("role") or message.get("type") or "") == "assistant"
        and not message.get("tool_calls")
    ]
    answers = [text for text in answers if text.strip()]

    turns: list[_RawTurn] = []
    cursor = 0
    for run in runs:
        turn = _RawTurn(user={"role": "user", "content": input_text(run.input)})
        if str(run.status) == RunStatus.SUCCESS and cursor < len(answers):
            turn.answers.append(answers[cursor])
            cursor += 1
        turns.append(turn)
    return turns


def _tool_entries(
    raw: _RawTurn, records: list[ToolCall]
) -> list[HistoryTool]:
    """工具条目以模型发起的调用为准，再按顺序用轨迹记录补状态与耗时。"""
    remaining = list(records)
    entries: list[HistoryTool] = []
    for call in raw.calls:
        name = str(call.get("name") or "")
        matched: ToolCall | None = None
        for candidate in remaining:
            if candidate.tool_code == name:
                matched = candidate
                break
        if matched is not None:
            remaining.remove(matched)
        entries.append(
            HistoryTool(
                name=name,
                status=str(matched.status) if matched is not None else TOOL_STATUS_UNKNOWN,
                latency_ms=matched.latency_ms if matched is not None else None,
                error=matched.error_message if matched is not None else None,
            )
        )
    # 轨迹里存在但 checkpoint 未留下调用的（例如工具抛错中断），也要如实呈现
    for leftover in remaining:
        entries.append(
            HistoryTool(
                name=leftover.tool_code,
                status=str(leftover.status),
                latency_ms=leftover.latency_ms,
                error=leftover.error_message,
            )
        )
    return entries


def _assemble(
    raw_turns: list[_RawTurn],
    runs: list[Run],
    tools: list[ToolCall],
    *,
    start_index: int = 1,
) -> list[HistoryTurn]:
    by_run: dict[uuid.UUID, list[ToolCall]] = {}
    for record in tools:
        by_run.setdefault(record.run_id, []).append(record)

    turns: list[HistoryTurn] = []
    for offset, raw in enumerate(raw_turns):
        index = start_index + offset
        # 段与 run 按开始时间顺序对齐；数量不一致时多出的 run 单独成轮、多出的段不带 run 元数据
        run = runs[offset] if offset < len(runs) else None
        user_text = _text(raw.user.get("content"))
        assistant_text = raw.answers[-1] if raw.answers else None
        turns.append(
            HistoryTurn(
                index=index,
                user=HistoryMessage(
                    content=user_text,
                    length=len(user_text),
                ),
                assistant=(
                    HistoryMessage(
                        content=assistant_text,
                        length=len(assistant_text),
                    )
                    if assistant_text is not None
                    else None
                ),
                tools=_tool_entries(raw, by_run.get(run.id, []) if run else []),
                run_id=run.id if run else None,
                trace_id=run.trace_id if run else None,
                status=str(run.status) if run else None,
                error=dict(run.error) if run and run.error else None,
                started_at=run.started_at if run else None,
            )
        )

    for extra in runs[len(raw_turns) :]:
        turns.append(
            HistoryTurn(
                index=start_index + len(turns),
                user=HistoryMessage(content=None, length=0),
                assistant=None,
                tools=_tool_entries(_RawTurn(), by_run.get(extra.id, [])),
                run_id=extra.id,
                trace_id=extra.trace_id,
                status=str(extra.status),
                error=dict(extra.error) if extra.error else None,
                started_at=extra.started_at,
            )
        )
    return turns


def _clamp(limit: int | None) -> tuple[int, bool]:
    if limit is None:
        return DEFAULT_TURNS_LIMIT, False
    clamped = max(1, min(int(limit), MAX_TURNS_LIMIT))
    return clamped, clamped != limit


class SessionHistoryService:
    def __init__(self) -> None:
        self._sessions = ExecutionSessionRepository()
        self._runs = RunRepository()
        self._tools = ToolCallRepository()

    async def get(
        self,
        *,
        actor: ActorContext,
        session_id: uuid.UUID,
        turns_limit: int | None = None,
    ) -> SessionHistory:
        limit, clamped = _clamp(turns_limit)
        async with session_scope(actor.tenant_id) as session:
            row = await self._sessions.by_id(
                session, tenant_id=actor.tenant_id, session_id=session_id
            )
            # 他人会话与不存在会话同响应，避免探测存在性
            if row is None or (row.created_by is not None and row.created_by != actor.actor_id):
                raise NotFoundError("session not found")
            runs = await self._runs.list_for_session(
                session,
                tenant_id=actor.tenant_id,
                execution_session_id=row.id,
            )
            tools = await self._tools.list_for_runs(
                session, tenant_id=actor.tenant_id, run_ids=[run.id for run in runs]
            )

        messages = await self._load_messages(row)
        raw_turns = _split_turns(messages)
        # 线程只覆盖最近若干轮：存量会话的早期轮次没有 user 消息锚点（甚至全无锚点），
        # 以运行记录补齐缺口，避免回放成空白或只剩最后一轮
        reconstructed = False
        if runs:
            missing = len(runs) - len(raw_turns)
            if missing > 0:
                if raw_turns:
                    lead = _turns_from_runs(runs[:missing], [])
                    raw_turns = [*lead, *raw_turns]
                else:
                    # 整个线程都没有锚点：assistant 消息还能按顺序配对给成功的运行
                    raw_turns = _turns_from_runs(runs, messages)
                reconstructed = True
        total = len(raw_turns)
        truncated = clamped or total > limit
        start_index = 1
        if total > limit:
            start_index = total - limit + 1
            raw_turns = raw_turns[-limit:]
        # 截断后轮次与 run 的对齐跟着尾部收敛
        aligned = (
            runs[len(runs) - len(raw_turns) :] if len(runs) >= len(raw_turns) else runs
        )
        turns = _assemble(
            raw_turns,
            aligned,
            tools,
            start_index=start_index,
        )
        return SessionHistory(
            session=row, turns=turns, truncated=truncated, reconstructed=reconstructed
        )

    async def _load_messages(self, row: ExecutionSession) -> list[dict[str, Any]]:
        saver = await checkpoint_store.saver()
        found = await saver.aget_tuple({"configurable": {"thread_id": f"sess-{row.id}"}})
        if found is None:
            return []
        checkpoint = found.checkpoint or {}
        messages = (checkpoint.get("channel_values") or {}).get("messages") or []
        return [item if isinstance(item, dict) else dict(item) for item in messages]


def session_expired(row: ExecutionSession, now: datetime | None = None) -> bool:
    return row.expires_at <= (now or datetime.now(UTC))
