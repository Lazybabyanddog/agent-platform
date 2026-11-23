"""历史裁剪策略：边界、顺序与孤儿工具消息。"""

from __future__ import annotations

from agent_platform.contexts.execution.application.session_service import (
    SESSION_KEY_PATTERN,
)
from agent_platform.contexts.execution.runtime.history import (
    estimate_message_tokens,
    estimate_tokens,
    trim_history,
)


def _message(role: str, content: str, **extra) -> dict:
    return {"role": role, "content": content, **extra}


def test_empty_history_is_untouched() -> None:
    assert trim_history(None, 4000) == ([], 0)
    assert trim_history([], 4000) == ([], 0)


def test_short_history_is_kept_whole() -> None:
    messages = [_message("user", "hi"), _message("assistant", "hello")]
    kept, dropped = trim_history(messages, 4000)
    assert kept == messages
    assert dropped == 0


def test_trimming_keeps_the_most_recent_messages() -> None:
    messages = [
        _message("user", "a" * 400),
        _message("assistant", "b" * 400),
        _message("user", "c" * 400),
    ]
    kept, dropped = trim_history(messages, 150)
    assert dropped >= 1
    assert kept[-1] == messages[-1]
    # 保留的是尾部连续片段
    assert kept == messages[len(messages) - len(kept) :]


def test_last_message_is_kept_even_over_limit() -> None:
    messages = [_message("user", "x" * 10_000)]
    kept, dropped = trim_history(messages, 256)
    assert kept == messages
    assert dropped == 0


def test_orphan_tool_results_are_dropped() -> None:
    messages = [
        _message("user", "hi"),
        _message("assistant", "", tool_calls=[{"id": "c1", "name": "echo", "arguments": {}}]),
        _message("tool", "result", tool_call_id="c1"),
        _message("assistant", "done"),
    ]
    kept, dropped = trim_history(messages, 12)
    assert kept[-1] == messages[-1]
    assert all(item["role"] != "tool" or item == messages[-1] for item in kept)
    assert dropped == len(messages) - len(kept)


def test_estimate_counts_tool_calls() -> None:
    plain = _message("assistant", "ok")
    with_tool = _message(
        "assistant", "ok", tool_calls=[{"id": "c1", "name": "search", "arguments": {"q": "x"}}]
    )
    assert estimate_message_tokens(with_tool) > estimate_message_tokens(plain)
    assert estimate_tokens([plain, with_tool]) == (
        estimate_message_tokens(plain) + estimate_message_tokens(with_tool)
    )


def test_session_key_pattern_boundaries() -> None:
    assert SESSION_KEY_PATTERN.fullmatch("chat-1")
    assert SESSION_KEY_PATTERN.fullmatch("a" * 128)
    assert not SESSION_KEY_PATTERN.fullmatch("")
    assert not SESSION_KEY_PATTERN.fullmatch("a" * 129)
    assert not SESSION_KEY_PATTERN.fullmatch("-leading")
    assert not SESSION_KEY_PATTERN.fullmatch("has space")
