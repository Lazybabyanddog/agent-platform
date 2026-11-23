"""LangGraph Postgres checkpointer 与 `execution.checkpoint_ref` 的接线。

checkpoint 内部结构由 LangGraph 管理；平台只保存可定位的引用，
便于 run 详情关联而不复制 checkpoint 内容。
"""

from __future__ import annotations

import asyncio
from typing import Any

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection
from psycopg.rows import dict_row

from agent_platform.settings import get_settings


def postgres_dsn() -> str:
    """checkpointer 需要 psycopg DSN；复用迁移账号（仅服务端使用）。"""
    return get_settings().migration_database_url.replace(
        "postgresql+asyncpg://", "postgresql://"
    )


class PostgresCheckpointStore:
    """按事件循环缓存 saver，避免连接跨 loop 复用。"""

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._saver: AsyncPostgresSaver | None = None
        self._lock = asyncio.Lock()

    async def saver(self) -> AsyncPostgresSaver:
        loop = asyncio.get_running_loop()
        if self._saver is not None and self._loop is loop:
            return self._saver
        async with self._lock:
            if self._saver is not None and self._loop is loop:
                return self._saver
            connection = await AsyncConnection.connect(
                postgres_dsn(),
                autocommit=True,
                prepare_threshold=0,
                row_factory=dict_row,
            )
            saver = AsyncPostgresSaver(connection)
            await saver.setup()
            self._saver = saver
            self._loop = loop
            return saver

    async def last_checkpoint_id(self, thread_id: str) -> str | None:
        saver = await self.saver()
        found = await saver.aget_tuple({"configurable": {"thread_id": thread_id}})
        if found is None:
            return None
        value: Any = (found.config.get("configurable") or {}).get("checkpoint_id")
        return str(value) if value else None


checkpoint_store = PostgresCheckpointStore()
