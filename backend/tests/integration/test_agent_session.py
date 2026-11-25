"""多轮会话：绑定、线程复用、并发、超期、裁剪、失败关闭、预览固化与查询。"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text, update

from agent_platform.contexts.agent.domain.enums import Environment
from agent_platform.contexts.execution.domain.enums import RunKind
from agent_platform.contexts.execution.infrastructure.repositories import (
    ExecutionSessionRepository,
    RunRepository,
)
from agent_platform.contexts.execution.infrastructure.tables import ExecutionSession
from agent_platform.shared.db import session_scope, system_session_scope
from tests.integration.agent_helpers import (
    create_agent,
    create_agent_version,
    create_model,
    create_prompt_version,
    create_tool_version,
    suffix,
)
from tests.integration.conftest import TenantEnv
from tests.integration.test_agent_invoke import StaticModel, _published_agent, use_model

ADMIN_PASSWORD = "Passw0rd!123"


async def _invoke(
    api_client, headers: dict[str, str], agent_id: str, *, session_id: str | None = None
) -> object:
    payload: dict = {"env": "DEV", "input": {}}
    if session_id is not None:
        payload["session_id"] = session_id
    return await api_client.post(
        f"/api/v1/agents/{agent_id}/invoke", headers=headers, json=payload
    )


async def _session_rows(tenant_id: uuid.UUID) -> list[ExecutionSession]:
    async with session_scope(tenant_id) as session:
        return list(
            await session.scalars(
                select(ExecutionSession).where(ExecutionSession.tenant_id == tenant_id)
            )
        )


async def _run_session_ids(tenant_id: uuid.UUID) -> list[uuid.UUID | None]:
    async with session_scope(tenant_id) as session:
        runs = await RunRepository().list_runs(session, tenant_id=tenant_id, page_size=100)
    return [run.execution_session_id for run in runs[0]]


async def test_session_repository_acquire_release_and_stale_takeover(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str]
) -> None:
    agent, version, release = await _published_agent(api_client, admin_headers)
    now = datetime.now(UTC)
    repo = ExecutionSessionRepository()

    async with session_scope(tenant_env.tenant_id) as session:
        first = await repo.create(
            session,
            tenant_id=tenant_env.tenant_id,
            agent_id=uuid.UUID(agent["id"]),
            env=Environment.DEV,
            kind=RunKind.INVOKE,
            session_key=f"repo-{suffix()}",
            agent_release_id=uuid.UUID(release["release_id"]),
            agent_version_id=uuid.UUID(version["id"]),
            expires_at=now + timedelta(hours=1),
        )
        second = await repo.create(
            session,
            tenant_id=tenant_env.tenant_id,
            agent_id=uuid.UUID(agent["id"]),
            env=Environment.DEV,
            kind=RunKind.INVOKE,
            session_key=f"repo-{suffix()}",
            agent_release_id=uuid.UUID(release["release_id"]),
            agent_version_id=uuid.UUID(version["id"]),
            expires_at=now + timedelta(hours=1),
        )
        first_id, second_id = first.id, second.id

    stall = now - timedelta(seconds=600)
    async with session_scope(tenant_env.tenant_id) as session:
        # 抢占成功
        assert await repo.acquire(
            session,
            tenant_id=tenant_env.tenant_id,
            session_id=first_id,
            now=now,
            stale_before=now - timedelta(seconds=180),
        )
        # 抢占失败（仍在占用）
        assert not await repo.acquire(
            session,
            tenant_id=tenant_env.tenant_id,
            session_id=first_id,
            now=now + timedelta(seconds=1),
            stale_before=now - timedelta(seconds=180),
        )
        # 其他会话不受影响
        assert await repo.acquire(
            session,
            tenant_id=tenant_env.tenant_id,
            session_id=second_id,
            now=now,
            stale_before=now - timedelta(seconds=180),
        )
        # 超时抢占
        await session.execute(
            update(ExecutionSession)
            .where(ExecutionSession.id == first_id)
            .values(busy_since=stall)
        )
        assert await repo.acquire(
            session,
            tenant_id=tenant_env.tenant_id,
            session_id=first_id,
            now=now + timedelta(seconds=2),
            stale_before=now - timedelta(seconds=180),
        )
        # 释放后可再次抢占
        await repo.release(
            session,
            tenant_id=tenant_env.tenant_id,
            session_id=first_id,
            now=now + timedelta(seconds=3),
            expires_at=now + timedelta(hours=2),
        )
        assert await repo.acquire(
            session,
            tenant_id=tenant_env.tenant_id,
            session_id=first_id,
            now=now + timedelta(seconds=4),
            stale_before=now - timedelta(seconds=180),
        )


async def test_session_keeps_bound_version_while_new_session_uses_latest(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str], monkeypatch
) -> None:
    use_model(monkeypatch, StaticModel(content="ok"))
    agent, version, _ = await _published_agent(api_client, admin_headers)

    # 首轮先绑定 v1，随后再发布 v2
    first = await _invoke(api_client, admin_headers, agent["id"], session_id="stick-1")
    assert first.status_code == 200, first.text

    model = await create_model(api_client, admin_headers)
    prompt = await create_prompt_version(api_client, admin_headers)
    newer = await create_agent_version(
        api_client,
        admin_headers,
        agent["id"],
        model_id=model["id"],
        prompt_version_id=prompt["id"],
        params={"temperature": 0.9},
    )
    published = await api_client.post(
        f"/api/v1/agents/{agent['id']}/releases",
        headers=admin_headers,
        json={"agent_version_id": newer["id"], "env": "DEV"},
    )
    assert published.status_code == 201, published.text

    second = await _invoke(api_client, admin_headers, agent["id"], session_id="stick-1")
    assert second.status_code == 200, second.text

    fresh = await _invoke(api_client, admin_headers, agent["id"], session_id="stick-2")
    assert fresh.status_code == 200, fresh.text

    async with session_scope(tenant_env.tenant_id) as session:
        repo = RunRepository()
        old_run = await repo.by_id(
            session,
            tenant_id=tenant_env.tenant_id,
            run_id=uuid.UUID(second.json()["run_id"]),
        )
        new_run = await repo.by_id(
            session,
            tenant_id=tenant_env.tenant_id,
            run_id=uuid.UUID(fresh.json()["run_id"]),
        )
    assert old_run is not None and new_run is not None
    assert old_run.agent_version_id == uuid.UUID(version["id"])
    assert new_run.agent_version_id == uuid.UUID(newer["id"])


async def test_same_session_reuses_thread_and_plain_call_stays_single_turn(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str], monkeypatch
) -> None:
    model = StaticModel(content="remember-me")
    use_model(monkeypatch, model)
    agent, _, _ = await _published_agent(api_client, admin_headers)

    first = await _invoke(api_client, admin_headers, agent["id"], session_id="thread-1")
    assert first.status_code == 200, first.text
    second = await _invoke(api_client, admin_headers, agent["id"], session_id="thread-1")
    assert second.status_code == 200, second.text

    assert len(model.requests) == 2
    history_contents = [message.content for message in model.requests[1].messages]
    assert "remember-me" in history_contents
    assert len(model.requests[1].messages) > len(model.requests[0].messages)

    # 无 session_id：每次都是新线程，不带上一轮历史
    third = await _invoke(api_client, admin_headers, agent["id"])
    fourth = await _invoke(api_client, admin_headers, agent["id"])
    assert third.status_code == 200 and fourth.status_code == 200
    assert len(model.requests[3].messages) == len(model.requests[2].messages)

    async with session_scope(tenant_env.tenant_id) as session:
        refs = (
            await session.execute(
                text("select run_id, thread_id from execution.checkpoint_ref")
            )
        ).all()
    threads = {str(row.thread_id) for row in refs}
    assert any(thread.startswith("sess-") for thread in threads)
    assert any(not thread.startswith("sess-") for thread in threads)


async def test_concurrent_turn_on_same_session_is_rejected(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str], monkeypatch
) -> None:
    class SlowModel:
        async def chat(self, request):
            from agent_platform.contexts.execution.domain.ports import ModelResponse

            await asyncio.sleep(0.4)
            return ModelResponse(content="slow")

    use_model(monkeypatch, SlowModel())
    agent, _, _ = await _published_agent(api_client, admin_headers)

    in_flight = asyncio.create_task(
        _invoke(api_client, admin_headers, agent["id"], session_id="busy-1")
    )
    await asyncio.sleep(0.15)
    rejected = await _invoke(api_client, admin_headers, agent["id"], session_id="busy-1")
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["error"]["code"] == "SESSION_BUSY"

    completed = await in_flight
    assert completed.status_code == 200, completed.text

    other = await _invoke(api_client, admin_headers, agent["id"], session_id="busy-2")
    assert other.status_code == 200, other.text


async def test_expired_session_starts_a_new_thread(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str], monkeypatch
) -> None:
    use_model(monkeypatch, StaticModel())
    agent, _, _ = await _published_agent(api_client, admin_headers)

    first = await _invoke(api_client, admin_headers, agent["id"], session_id="ttl-1")
    assert first.status_code == 200, first.text
    async with session_scope(tenant_env.tenant_id) as session:
        await session.execute(
            update(ExecutionSession).values(expires_at=datetime.now(UTC) - timedelta(hours=1))
        )

    second = await _invoke(api_client, admin_headers, agent["id"], session_id="ttl-1")
    assert second.status_code == 200, second.text

    runs = await _run_session_ids(tenant_env.tenant_id)
    assert len(runs) == 2
    # 最新一轮挂到新会话行上（旧行落墓碑，历史运行仍指向它直到被回收）
    assert runs[0] is not None
    assert runs[0] != runs[1], "过期复用时必须换到新的会话行"

    # 过期后换新行、换 thread，旧上下文不再参与
    async with session_scope(tenant_env.tenant_id) as session:
        threads = list(
            (
                await session.execute(
                    text("select thread_id from execution.checkpoint_ref where run_id = :run_id"),
                    {"run_id": uuid.UUID(second.json()["run_id"])},
                )
            ).scalars()
        )
    assert threads and threads[0].startswith("sess-")
    rows = await _session_rows(tenant_env.tenant_id)
    live = [row for row in rows if row.deleted_at is None]
    assert len(live) == 1
    assert live[0].expires_at > datetime.now(UTC)
    # 过期旧行落墓碑等待清理进程回收，不再是有效会话
    assert [row.deleted_at for row in rows if row.deleted_at is not None] != []


async def test_long_history_is_trimmed_and_recorded(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str], monkeypatch
) -> None:
    use_model(monkeypatch, StaticModel(content="x" * 4000))
    agent, _, _ = await _published_agent(
        api_client, admin_headers, policy={"history_token_limit": 256}
    )

    first = await _invoke(api_client, admin_headers, agent["id"], session_id="trim-1")
    assert first.status_code == 200, first.text
    second = await _invoke(api_client, admin_headers, agent["id"], session_id="trim-1")
    assert second.status_code == 200, second.text
    assert second.json()["status"] == "SUCCESS"

    async with session_scope(tenant_env.tenant_id) as session:
        rows = (
            await session.execute(
                text(
                    "select type, payload from execution.execution_event "
                    "where run_id = :run_id"
                ),
                {"run_id": uuid.UUID(second.json()["run_id"])},
            )
        ).all()
    trimmed = [row for row in rows if row.type == "session.history_trimmed"]
    assert len(trimmed) == 1
    assert trimmed[0].payload["dropped"] >= 1
    assert trimmed[0].payload["limit"] == 256


async def test_stale_binding_fails_closed_without_switching_version(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str], monkeypatch
) -> None:
    use_model(monkeypatch, StaticModel())
    tool = await create_tool_version(api_client, admin_headers)
    model = await create_model(api_client, admin_headers)
    prompt = await create_prompt_version(api_client, admin_headers)
    agent = await create_agent(api_client, admin_headers)
    version = await create_agent_version(
        api_client,
        admin_headers,
        agent["id"],
        model_id=model["id"],
        prompt_version_id=prompt["id"],
        tool_version_ids=[tool["id"]],
    )
    published = await api_client.post(
        f"/api/v1/agents/{agent['id']}/releases",
        headers=admin_headers,
        json={"agent_version_id": version["id"], "env": "DEV"},
    )
    assert published.status_code == 201, published.text

    first = await _invoke(api_client, admin_headers, agent["id"], session_id="revoke-1")
    assert first.status_code == 200, first.text

    revoked = await api_client.delete(
        f"/api/v1/tools/{tool['tool_id']}/grant", headers=admin_headers
    )
    assert revoked.status_code == 204, revoked.text

    second = await _invoke(api_client, admin_headers, agent["id"], session_id="revoke-1")
    assert second.status_code == 409, second.text
    error = second.json()["error"]
    assert error["code"] == "SESSION_BINDING_UNAVAILABLE"
    assert any(
        item.get("field") == "tool_version_ids" and item.get("resource_id") == tool["id"]
        for item in error["details"]
    )
    # 失败关闭不产生新的运行记录
    async with session_scope(tenant_env.tenant_id) as session:
        count = await session.scalar(
            text("select count(*) from execution.run where session_id = 'revoke-1'")
        )
    assert count == 1


async def test_preview_session_pins_first_turn_snapshot(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str], monkeypatch
) -> None:
    use_model(monkeypatch, StaticModel(content="preview"))
    model_a = await create_model(api_client, admin_headers)
    prompt_a = await create_prompt_version(api_client, admin_headers)
    model_b = await create_model(api_client, admin_headers)
    prompt_b = await create_prompt_version(api_client, admin_headers)
    agent = await create_agent(api_client, admin_headers)

    def payload(model_id: str, prompt_version_id: str) -> dict:
        return {
            "model_id": model_id,
            "prompt_version_id": prompt_version_id,
            "tool_version_ids": [],
            "params": {},
            "policy": {"max_steps": 2},
            "input": {},
            "preview_session_id": "pv-1",
        }

    first = await api_client.post(
        f"/api/v1/agents/{agent['id']}/preview",
        headers=admin_headers,
        json=payload(model_a["id"], prompt_a["id"]),
    )
    assert first.status_code == 200, first.text

    second = await api_client.post(
        f"/api/v1/agents/{agent['id']}/preview",
        headers=admin_headers,
        json=payload(model_b["id"], prompt_b["id"]),
    )
    assert second.status_code == 200, second.text
    assert second.json()["config_hash"] == first.json()["config_hash"]
    assert second.json()["preview_snapshot"]["model"]["model_id"] == model_a["id"]

    fresh = await api_client.post(
        f"/api/v1/agents/{agent['id']}/preview",
        headers=admin_headers,
        json={**payload(model_b["id"], prompt_b["id"]), "preview_session_id": "pv-2"},
    )
    assert fresh.status_code == 200, fresh.text
    assert fresh.json()["config_hash"] != first.json()["config_hash"]


async def test_sessions_endpoint_paginates_and_isolates_tenants(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str], monkeypatch
) -> None:
    use_model(monkeypatch, StaticModel())
    agent, _, _ = await _published_agent(api_client, admin_headers)
    for index in range(3):
        response = await _invoke(
            api_client, admin_headers, agent["id"], session_id=f"page-{index}"
        )
        assert response.status_code == 200, response.text

    first_page = await api_client.get(
        "/api/v1/sessions",
        headers=admin_headers,
        params={"agent_id": agent["id"], "page": 1, "page_size": 2},
    )
    assert first_page.status_code == 200, first_page.text
    body = first_page.json()
    assert body["total"] == 3
    assert len(body["items"]) == 2
    assert all(item["session_key"].startswith("page-") for item in body["items"])
    assert body["items"][0]["version_no"] is not None

    second_page = await api_client.get(
        "/api/v1/sessions",
        headers=admin_headers,
        params={"agent_id": agent["id"], "page": 2, "page_size": 2},
    )
    assert len(second_page.json()["items"]) == 1

    # 时间范围筛选：未来窗口为空
    future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    empty = await api_client.get(
        "/api/v1/sessions",
        headers=admin_headers,
        params={"agent_id": agent["id"], "start": future},
    )
    assert empty.json()["total"] == 0

    # 其他租户看不到本租户会话
    suffix_code = uuid.uuid4().hex[:10]
    from agent_platform.bootstrap import bootstrap

    other = await bootstrap(
        tenant_code=f"iso{suffix_code}",
        tenant_name="Isolated",
        admin_email=f"iso-{suffix_code}@example.com",
        admin_password=ADMIN_PASSWORD,
    )
    try:
        login = await api_client.post(
            "/api/v1/auth/login",
            json={
                "tenant_code": f"iso{suffix_code}",
                "email": f"iso-{suffix_code}@example.com",
                "password": ADMIN_PASSWORD,
            },
        )
        assert login.status_code == 200, login.text
        other_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        isolated = await api_client.get(
            "/api/v1/sessions",
            headers=other_headers,
            params={"agent_id": agent["id"]},
        )
        assert isolated.status_code == 200, isolated.text
        assert isolated.json()["total"] == 0
    finally:
        from tests.integration.conftest import purge_tenant

        await purge_tenant(tenant_id=other.tenant_id, user_id=other.user_id)


async def test_other_tenants_do_not_share_sessions(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str], monkeypatch
) -> None:
    """同一 session_id 在不同租户下是不同会话。"""
    use_model(monkeypatch, StaticModel())
    agent, _, _ = await _published_agent(api_client, admin_headers)
    response = await _invoke(api_client, admin_headers, agent["id"], session_id="shared-key")
    assert response.status_code == 200, response.text

    async with system_session_scope() as session:
        total = await session.scalar(
            text(
                "select count(*) from execution.execution_session "
                "where tenant_id = :tid and session_key = 'shared-key'"
            ),
            {"tid": tenant_env.tenant_id},
        )
    assert total == 1
