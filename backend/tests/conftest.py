from __future__ import annotations

import pytest

from agent_platform.shared.db import dispose_engines
from agent_platform.shared.eventloop import ensure_psycopg_compatible_loop

# 必须在 pytest-asyncio 创建 session 级事件循环之前切换（psycopg 不支持 Proactor）
ensure_psycopg_compatible_loop()


@pytest.fixture(scope="session", autouse=True)
async def _dispose_db_engines():
    yield
    await dispose_engines()
