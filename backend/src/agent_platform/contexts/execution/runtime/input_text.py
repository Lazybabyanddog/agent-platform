"""把本次输入折叠成一段可读文本。

运行时用它把"没有落点的输入"补成一条用户消息；会话历史重建也复用它，
保证写入与读取两侧对同一次输入的解读一致。
"""

from __future__ import annotations

import json
from typing import Any


def _readable(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def input_text(
    values: dict[str, Any] | None,
    variables: list[dict[str, Any]] | None = None,
) -> str:
    """单值取原文；多值按 Prompt 声明的变量顺序逐行写成 ``name: value``。"""
    values = values or {}
    declared = [
        str(item.get("name"))
        for item in (variables or [])
        if isinstance(item, dict) and item.get("name")
    ]
    # 模板声明顺序优先，未声明的键按本次输入顺序补在后面
    names = [name for name in declared if name in values]
    names.extend(name for name in values if name not in names)

    items = [(name, _readable(values[name])) for name in names]
    items = [(name, text) for name, text in items if text.strip()]
    if not items:
        return ""
    if len(items) == 1 and isinstance(values[items[0][0]], str):
        return items[0][1]
    return "\n".join(f"{name}: {text}" for name, text in items)


__all__ = ["input_text"]
