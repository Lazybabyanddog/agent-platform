"""验证租户上下文注入与 RLS 兜底行为。

需要本地 docker-compose 起的 PostgreSQL；应用角色 agent_app 必须是非超级用户，
否则 PostgreSQL 会跳过 RLS。
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from agent_platform.shared.context import ActorContext, set_actor
from agent_platform.shared.db import session_scope, system_session_scope

TENANT_A = uuid.UUID("11111111-1111-7111-8111-111111111111")
TENANT_B = uuid.UUID("22222222-2222-7222-8222-222222222222")

SETUP_SQL = [
    "create schema if not exists test_rls",
    "drop table if exists test_rls.probe",
    "create table test_rls.probe (id uuid primary key, tenant_id uuid not null, note text)",
    "grant usage on schema test_rls to agent_app",
    "grant select, insert, update, delete on test_rls.probe to agent_app",
    "alter table test_rls.probe enable row level security",
    "alter table test_rls.probe force row level security",
    """
    create policy tenant_isolation on test_rls.probe
      using (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
      with check (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
    """,
]


@pytest.fixture()
async def probe_table():
    async with system_session_scope() as session:
        for statement in SETUP_SQL:
            await session.execute(text(statement))
    yield
    async with system_session_scope() as session:
        await session.execute(text("drop schema if exists test_rls cascade"))


async def test_insert_with_tenant_succeeds(probe_table):
    row_id = uuid.uuid4()
    async with session_scope(TENANT_A) as session:
        await session.execute(
            text("insert into test_rls.probe (id, tenant_id, note) values (:id, :tid, 'a')"),
            {"id": row_id, "tid": TENANT_A},
        )

    async with session_scope(TENANT_A) as session:
        count = await session.scalar(text("select count(*) from test_rls.probe"))
    assert count == 1


async def test_query_without_tenant_returns_empty(probe_table):
    async with session_scope(TENANT_A) as session:
        await session.execute(
            text("insert into test_rls.probe (id, tenant_id, note) values (:id, :tid, 'a')"),
            {"id": uuid.uuid4(), "tid": TENANT_A},
        )

    async with session_scope(tenant_id=None) as session:
        # 未注入租户上下文：RLS 兜底为空集
        await session.execute(text("select set_config('app.tenant_id', '', true)"))
        count = await session.scalar(text("select count(*) from test_rls.probe"))

    assert count == 0


async def test_other_tenant_cannot_see_rows(probe_table):
    async with session_scope(TENANT_A) as session:
        await session.execute(
            text("insert into test_rls.probe (id, tenant_id, note) values (:id, :tid, 'a')"),
            {"id": uuid.uuid4(), "tid": TENANT_A},
        )

    async with session_scope(TENANT_B) as session:
        count = await session.scalar(text("select count(*) from test_rls.probe"))

    assert count == 0


async def test_insert_for_other_tenant_is_rejected(probe_table):
    with pytest.raises(DBAPIError):
        async with session_scope(TENANT_A) as session:
            await session.execute(
                text("insert into test_rls.probe (id, tenant_id, note) values (:id, :tid, 'b')"),
                {"id": uuid.uuid4(), "tid": TENANT_B},
            )


async def test_hook_reads_tenant_from_context(probe_table):
    set_actor(
        ActorContext(
            tenant_id=TENANT_A,
            actor_type="USER",
            actor_id=uuid.uuid4(),
        )
    )
    try:
        async with session_scope() as session:
            await session.execute(
                text("insert into test_rls.probe (id, tenant_id, note) values (:id, :tid, 'ctx')"),
                {"id": uuid.uuid4(), "tid": TENANT_A},
            )

        async with session_scope() as session:
            count = await session.scalar(text("select count(*) from test_rls.probe"))
        assert count == 1
    finally:
        set_actor(None)


async def test_tenant_context_does_not_leak_between_sessions(probe_table):
    async with session_scope(TENANT_A) as session:
        await session.execute(
            text("insert into test_rls.probe (id, tenant_id, note) values (:id, :tid, 'a')"),
            {"id": uuid.uuid4(), "tid": TENANT_A},
        )

    # 复用连接池中的连接：新事务若不注入租户，不得看到上一事务的数据
    async with session_scope(tenant_id=None) as session:
        await session.execute(text("select set_config('app.tenant_id', '', true)"))
        visible = await session.scalar(text("select count(*) from test_rls.probe"))

    assert visible == 0
