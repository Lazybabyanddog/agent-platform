"""agent console 的数据约束：会话唯一键、运行种类约束与状态回填。"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from agent_platform.shared.db import system_session_scope
from tests.integration.agent_helpers import (
    create_agent,
    create_agent_version,
    create_model,
    create_prompt_version,
)

pytestmark = pytest.mark.asyncio


async def test_execution_session_unique_scope_and_indexes() -> None:
    async with system_session_scope() as session:
        unique_names = {
            row[0]
            for row in (
                await session.execute(
                    text(
                        "select conname from pg_constraint "
                        "where conrelid = 'execution.execution_session'::regclass and contype = 'u'"
                    )
                )
            ).all()
        }
        indexes = {
            row[0]
            for row in (
                await session.execute(
                    text(
                        "select indexname from pg_indexes "
                        "where schemaname = 'execution' and tablename = 'execution_session'"
                    )
                )
            ).all()
        }

    assert "uq_execution_session_scope" in unique_names
    assert "ix_execution_session_tenant_agent_active" in indexes
    assert "ix_execution_session_tenant_agent_owner_active" in indexes
    assert "ix_execution_session_expires_at" in indexes


async def test_session_history_metadata_columns_default(
    api_client, admin_headers, tenant_env
) -> None:
    agent = await create_agent(api_client, admin_headers)
    async with system_session_scope() as session:
        columns = {
            row[0]
            for row in (
                await session.execute(
                    text(
                        "select column_name from information_schema.columns "
                        "where table_schema = 'execution' and table_name = 'execution_session'"
                    )
                )
            ).all()
        }
        assert {"title", "turn_count", "created_by"} <= columns
        row = (
            await session.execute(
                text(
                    "insert into execution.execution_session "
                    "(id, tenant_id, agent_id, env, session_key, kind, preview_snapshot,"
                    " status, last_active_at, expires_at) "
                    "values (gen_random_uuid(), :tid, :aid, 'DEV', 'history-default', 'PREVIEW',"
                    " '{\"schema\": 1}'::jsonb, 'IDLE', now(), now() + interval '1 day') "
                    "returning title, turn_count, created_by"
                ),
                {"tid": str(tenant_env.tenant_id), "aid": agent["id"]},
            )
        ).one()
    assert row[0] is None
    assert row[1] == 0
    assert row[2] is None


async def test_execution_session_scope_is_unique_per_kind(
    api_client, admin_headers, tenant_env
) -> None:
    agent = await create_agent(api_client, admin_headers)
    scope = {
        "tid": str(tenant_env.tenant_id),
        "aid": agent["id"],
        "key": "chat-1",
    }

    async def insert() -> None:
        async with system_session_scope() as session:
            await session.execute(
                text(
                    "insert into execution.execution_session "
                    "(id, tenant_id, agent_id, env, session_key, kind, preview_snapshot,"
                    " status, last_active_at, expires_at) "
                    "values (gen_random_uuid(), :tid, :aid, 'DEV', :key, 'PREVIEW',"
                    " '{\"schema\": 1}'::jsonb, 'IDLE', now(), now() + interval '1 day')"
                ),
                scope,
            )

    await insert()
    with pytest.raises(IntegrityError):
        await insert()


async def test_preview_run_requires_snapshot_and_no_release(
    api_client, admin_headers, tenant_env
) -> None:
    agent = await create_agent(api_client, admin_headers)
    base = {
        "tid": str(tenant_env.tenant_id),
        "aid": agent["id"],
        "caller": str(uuid.uuid4()),
        "trace": str(uuid.uuid4()),
    }

    async with system_session_scope() as session:
        await session.execute(
            text(
                "insert into execution.run "
                "(id, tenant_id, agent_id, kind, env, caller_type, caller_id, input,"
                " status, trace_id, preview_snapshot) "
                "values (gen_random_uuid(), :tid, :aid, 'PREVIEW', 'DEV', 'USER', :caller,"
                " '{}'::jsonb, 'SUCCESS', :trace, '{\"schema\": 1}'::jsonb)"
            ),
            base,
        )

    with pytest.raises(IntegrityError):
        async with system_session_scope() as session:
            await session.execute(
                text(
                    "insert into execution.run "
                    "(id, tenant_id, agent_id, kind, env, caller_type, caller_id, input,"
                    " status, trace_id) "
                    "values (gen_random_uuid(), :tid, :aid, 'INVOKE', 'DEV', 'USER', :caller,"
                    " '{}'::jsonb, 'SUCCESS', :trace)"
                ),
                base,
            )


async def test_backfill_marks_published_agents_active_and_is_idempotent(
    api_client, admin_headers, tenant_env
) -> None:
    model = await create_model(api_client, admin_headers)
    prompt = await create_prompt_version(api_client, admin_headers)
    agent = await create_agent(api_client, admin_headers)
    version = await create_agent_version(
        api_client,
        admin_headers,
        agent["id"],
        model_id=model["id"],
        prompt_version_id=prompt["id"],
    )
    released = await api_client.post(
        f"/api/v1/agents/{agent['id']}/releases",
        headers=admin_headers,
        json={"agent_version_id": version["id"], "env": "DEV"},
    )
    assert released.status_code == 201, released.text

    async with system_session_scope() as session:
        # 模拟升级前的数据：已发布但状态仍为草稿
        await session.execute(
            text("update agent.agent set status = 'DRAFT' where id = :aid"),
            {"aid": agent["id"]},
        )

    statement = text(
        """
        update agent.agent a
           set status = 'ACTIVE'
         where a.status = 'DRAFT'
           and exists (select 1 from agent.agent_release r where r.agent_id = a.id)
        """
    )
    async with system_session_scope() as session:
        first = (await session.execute(statement)).rowcount
        second = (await session.execute(statement)).rowcount

    async with system_session_scope() as session:
        status = (
            await session.execute(
                text("select status from agent.agent where id = :aid"), {"aid": agent["id"]}
            )
        ).scalar_one()

    assert first >= 1
    assert second == 0
    assert status == "ACTIVE"
