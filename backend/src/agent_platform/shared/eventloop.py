"""事件循环兼容性。

psycopg 的异步模式在 Windows 上不支持 ProactorEventLoop，
而 LangGraph 的 Postgres checkpointer 依赖 psycopg 异步连接。
必须在事件循环创建之前切换为 Selector 策略，否则执行链路不可用。
"""

from __future__ import annotations

import asyncio
import sys


def ensure_psycopg_compatible_loop() -> None:
    if sys.platform != "win32":
        return
    policy_factory = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
    if policy_factory is None:
        return
    if not isinstance(asyncio.get_event_loop_policy(), policy_factory):
        asyncio.set_event_loop_policy(policy_factory())
