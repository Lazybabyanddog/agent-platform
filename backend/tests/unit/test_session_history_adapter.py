"""会话历史 adapter 的契约测试：消息原语 → 结构化轮次。

history 读取依赖 checkpoint 里消息的形态，这里固定几份序列，升级 runtime
或 LangGraph 时先跑这组用例，能在接口层之前暴露结构变化。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

from agent_platform.contexts.execution.application.session_history_service import (
    _assemble,
    _split_turns,
    _tool_entries,
    _turns_from_runs,
)

NOW = datetime(2026, 11, 24, tzinfo=UTC)


def _run(
    *,
    status: str = "SUCCESS",
    error: dict | None = None,
    input_values: dict | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        trace_id=f"trace-{uuid.uuid4().hex[:6]}",
        status=status,
        error=error,
        started_at=NOW,
        input=input_values,
    )


def _tool_call_record(
    *, run_id: uuid.UUID, tool_code: str, status: str = "SUCCESS"
) -> SimpleNamespace:
    return SimpleNamespace(
        run_id=run_id,
        tool_code=tool_code,
        status=status,
        latency_ms=12,
        error_message=None if status == "SUCCESS" else "boom",
    )


TOOL_THREAD = [
    {"role": "system", "content": "system prompt"},
    {"role": "user", "content": "查订单"},
    {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"id": "call-1", "name": "order_lookup", "arguments": {"id": 1}}],
    },
    {"role": "tool", "content": '{"ok": true}', "name": "order_lookup", "tool_call_id": "call-1"},
    {"role": "assistant", "content": "订单已发货"},
]


def test_split_turns_groups_by_user_and_skips_system() -> None:
    turns = _split_turns(TOOL_THREAD)
    assert len(turns) == 1
    assert turns[0].user["content"] == "查订单"
    assert turns[0].answers == ["订单已发货"]
    assert [call["id"] for call in turns[0].calls] == ["call-1"]
    assert len(turns[0].tool_messages) == 1


def test_split_turns_keeps_multiple_turns_in_order() -> None:
    thread = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "第一问"},
        {"role": "assistant", "content": "第一答"},
        {"role": "user", "content": "第二问"},
        {"role": "assistant", "content": "第二答"},
    ]
    turns = _split_turns(thread)
    assert [turn.user["content"] for turn in turns] == ["第一问", "第二问"]
    assert [turn.answers[-1] for turn in turns] == ["第一答", "第二答"]


def test_tool_entries_pair_by_name_and_fall_back_to_unknown() -> None:
    raw = _split_turns(TOOL_THREAD)[0]
    run_id = uuid.uuid4()
    paired = _tool_entries(raw, [_tool_call_record(run_id=run_id, tool_code="order_lookup")])
    assert [(entry.name, entry.status, entry.latency_ms) for entry in paired] == [
        ("order_lookup", "SUCCESS", 12)
    ]
    unknown = _tool_entries(raw, [])
    assert unknown[0].status == "UNKNOWN"
    assert unknown[0].latency_ms is None


def test_assemble_keeps_content_without_answer_and_surfaces_failed_tool() -> None:
    thread = [
        {"role": "user", "content": "调用会失败"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call-9", "name": "risky_tool", "arguments": {}}],
        },
    ]
    run = _run(status="FAILED", error={"code": "TOOL_FAILED", "message": "boom"})
    turns = _assemble(
        _split_turns(thread),
        [run],
        [_tool_call_record(run_id=run.id, tool_code="risky_tool", status="FAILED")],
    )
    assert len(turns) == 1
    assert turns[0].assistant is None
    assert turns[0].tools[0].status == "FAILED"
    assert turns[0].tools[0].error == "boom"
    assert turns[0].status == "FAILED"
    assert turns[0].error == {"code": "TOOL_FAILED", "message": "boom"}


def test_assemble_tolerates_run_and_turn_mismatch() -> None:
    thread = [
        {"role": "user", "content": "有记录的一轮"},
        {"role": "assistant", "content": "答"},
        {"role": "user", "content": "没有 run 的一轮"},
        {"role": "assistant", "content": "答"},
    ]
    runs = [_run(), _run(), _run(status="FAILED", error={"code": "TIMEOUT"})]
    turns = _assemble(_split_turns(thread), runs, [])
    assert [turn.index for turn in turns] == [1, 2, 3]
    assert turns[0].run_id == runs[0].id
    assert turns[1].run_id == runs[1].id
    assert turns[2].run_id == runs[2].id
    assert turns[2].assistant is None
    assert turns[2].error == {"code": "TIMEOUT"}

    fewer = _assemble(_split_turns(thread), [_run()], [])
    assert len(fewer) == 2
    assert fewer[1].run_id is None
    assert fewer[1].assistant is not None


def test_assemble_returns_content_with_lengths() -> None:
    turns = _assemble(_split_turns(TOOL_THREAD), [_run()], [])
    assert turns[0].user.content == "查订单"
    assert turns[0].user.length == len("查订单")
    assert turns[0].assistant is not None
    assert turns[0].assistant.content == "订单已发货"
    assert turns[0].assistant.length == len("订单已发货")
    assert turns[0].tools[0].name == "order_lookup"


SYSTEM_ASSISTANT_THREAD = [
    {"role": "system", "content": "你是天气助手"},
    {"role": "assistant", "content": "武汉明天多云"},
    {"role": "assistant", "content": "北京明天晴"},
]


def test_turns_from_runs_pairs_runs_with_answers() -> None:
    """线程里只有 system + assistant 时，以运行记录为锚还原轮次。"""
    runs = [
        _run(input_values={"day": "武汉明天天气"}),
        _run(input_values={"day": "北京明天天气"}),
        _run(
            status="FAILED",
            error={"code": "TIMEOUT", "message": "upstream timeout"},
            input_values={"day": "上海明天天气"},
        ),
    ]
    raw = _turns_from_runs(runs, SYSTEM_ASSISTANT_THREAD)
    assert len(raw) == len(runs)
    assert raw[0].user["content"] == "武汉明天天气"
    assert raw[1].answers == ["北京明天晴"]
    # 失败的运行不消费回答，缺失就留空
    assert raw[2].answers == []

    turns = _assemble(raw, runs, [])
    assert [turn.index for turn in turns] == [1, 2, 3]
    assert [turn.user.content for turn in turns] == [
        "武汉明天天气",
        "北京明天天气",
        "上海明天天气",
    ]
    assert turns[0].assistant is not None
    assert turns[0].assistant.content == "武汉明天多云"
    assert turns[1].assistant is not None
    assert turns[1].assistant.content == "北京明天晴"
    assert turns[2].assistant is None
    assert turns[2].status == "FAILED"
    assert turns[2].error == {"code": "TIMEOUT", "message": "upstream timeout"}


def test_turns_from_runs_joins_multiple_input_values() -> None:
    runs = [_run(input_values={"city": "武汉", "days": 2})]
    raw = _turns_from_runs(runs, [{"role": "assistant", "content": "行程如下"}])
    assert raw[0].user["content"] == "city: 武汉\ndays: 2"
    assert raw[0].answers == ["行程如下"]


def test_turns_from_runs_tolerates_missing_answers_and_input() -> None:
    runs = [_run(input_values=None), _run(input_values={"q": "第二问"})]
    raw = _turns_from_runs(runs, [{"role": "system", "content": "sys"}])
    assert [turn.user["content"] for turn in raw] == ["", "第二问"]
    assert all(turn.answers == [] for turn in raw)
