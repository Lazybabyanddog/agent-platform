"""多轮历史裁剪：按字符数粗估 token，保留最近内容并去掉孤立的工具结果。

估算只用于控制送入模型的历史规模，不参与计费或预算；真正的 token 用量仍以
模型返回为准。裁剪从最新消息往前累加，超出上限即停止，因此"最近"总是优先保留。
"""

from __future__ import annotations

import json
from typing import Any

CHARS_PER_TOKEN = 4
MESSAGE_OVERHEAD_TOKENS = 4


def _estimate_text(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // CHARS_PER_TOKEN)


def estimate_message_tokens(message: dict[str, Any]) -> int:
    total = MESSAGE_OVERHEAD_TOKENS
    total += _estimate_text(str(message.get("content") or ""))
    for call in message.get("tool_calls") or []:
        arguments = json.dumps(call.get("arguments") or {}, ensure_ascii=False, default=str)
        total += MESSAGE_OVERHEAD_TOKENS
        total += _estimate_text(str(call.get("name") or ""))
        total += _estimate_text(arguments)
    return total


def estimate_tokens(messages: list[dict[str, Any]]) -> int:
    return sum(estimate_message_tokens(message) for message in messages)


def trim_history(
    messages: list[dict[str, Any]] | None, limit: int
) -> tuple[list[dict[str, Any]], int]:
    """返回 `(保留的历史, 裁剪条数)`；至少保留最后一条消息。"""
    if not messages:
        return [], 0

    kept: list[dict[str, Any]] = []
    used = 0
    for message in reversed(messages):
        cost = estimate_message_tokens(message)
        if kept and used + cost > limit:
            break
        kept.append(message)
        used += cost
    kept.reverse()

    dropped = len(messages) - len(kept)
    # 裁剪点落在工具结果上会留下没有对应 assistant 调用的孤儿消息，模型侧会拒绝
    while len(kept) > 1 and kept[0].get("role") == "tool":
        kept.pop(0)
        dropped += 1
    return kept, dropped


__all__ = [
    "CHARS_PER_TOKEN",
    "MESSAGE_OVERHEAD_TOKENS",
    "estimate_message_tokens",
    "estimate_tokens",
    "trim_history",
]
