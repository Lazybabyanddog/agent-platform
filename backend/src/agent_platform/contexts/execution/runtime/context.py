"""运行时上下文：把版本声明的事实变成模型可见的消息。

上下文值是平台事实，不是用户输入：因此它独立成一条消息，不要求模板写占位符，
也不写入会话历史（否则会按轮次累积，且被当成对话内容）。
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

# 平台时区由部署环境提供；未设置时使用服务器本地时区
ENV_TIMEZONE = "AGENT_PLATFORM_TIMEZONE"
_WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def _timezone_label(moment: datetime) -> str:
    return os.getenv(ENV_TIMEZONE) or moment.tzname() or "UTC"


def build_context_message(
    items: list[str] | tuple[str, ...] | None,
    *,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """按声明渲染上下文消息；未声明任何项时返回 ``None``。

    ``now`` 只用于测试注入固定时钟；生产由服务器当前时间产生，
    并且同一次执行只取一次，避免跨时间边界时轮内取值漂移。
    """
    if not items:
        return None
    moment = now or datetime.now().astimezone()
    if moment.tzinfo is None:
        moment = moment.astimezone()

    lines: list[str] = []
    if "now" in items:
        lines.append(
            f"当前时间：{moment.isoformat(timespec='seconds')}（{_WEEKDAYS[moment.weekday()]}）"
        )
    if "timezone" in items:
        lines.append(f"时区：{_timezone_label(moment)}")
    if not lines:
        return None
    return {"role": "system", "content": "\n".join(lines), "tool_calls": []}


def with_context(
    messages: list[dict[str, Any]], context: dict[str, Any] | None
) -> list[dict[str, Any]]:
    """把上下文插在系统消息之后、历史之前。

    位置靠前有两个原因：它属于本轮全局约束，不是对话的一部分；
    模板前缀不变，既有 Prompt 的前缀缓存仍然命中。
    """
    if not context:
        return messages
    if not messages:
        return [context]
    return [*messages[:1], context, *messages[1:]]


__all__ = ["build_context_message", "with_context"]
