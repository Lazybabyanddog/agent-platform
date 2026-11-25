"""Agent 生命周期：发布激活、启停门禁、归档终态、软删除与审计。"""

from __future__ import annotations

from typing import Any

import httpx
from sqlalchemy import text

from agent_platform.shared.db import session_scope
from tests.integration.agent_helpers import (
    create_agent,
    create_agent_version,
    create_model,
    create_prompt_version,
    suffix,
)
from tests.integration.conftest import TenantEnv


async def _publish(
    api_client: httpx.AsyncClient,
    headers: dict[str, str],
    agent_id: str,
    version_id: str,
    *,
    env: str = "DEV",
) -> httpx.Response:
    return await api_client.post(
        f"/api/v1/agents/{agent_id}/releases",
        headers=headers,
        json={"agent_version_id": version_id, "env": env},
    )


async def _published_agent(
    api_client: httpx.AsyncClient, headers: dict[str, str]
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    model = await create_model(api_client, headers)
    prompt_version = await create_prompt_version(api_client, headers)
    agent = await create_agent(api_client, headers)
    version = await create_agent_version(
        api_client,
        headers,
        agent["id"],
        model_id=model["id"],
        prompt_version_id=prompt_version["id"],
    )
    published = await _publish(api_client, headers, agent["id"], version["id"])
    assert published.status_code == 201, published.text
    return agent, version, published.json()


async def _run_count(tenant_id) -> int:
    async with session_scope(tenant_id) as session:
        return int(
            await session.scalar(text("select count(*) from execution.run")) or 0
        )


async def test_first_publish_activates_agent(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str]
) -> None:
    agent, _, release = await _published_agent(api_client, admin_headers)
    assert agent["status"] == "DRAFT"
    assert release["version_no"] == 1

    fetched = await api_client.get(
        f"/api/v1/agents/{agent['id']}", headers=admin_headers
    )
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["status"] == "ACTIVE"


async def test_deactivate_blocks_invocation_and_activation_restores(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str]
) -> None:
    agent, _, _ = await _published_agent(api_client, admin_headers)

    stopped = await api_client.post(
        f"/api/v1/agents/{agent['id']}/deactivate", headers=admin_headers
    )
    assert stopped.status_code == 200, stopped.text
    assert stopped.json()["status"] == "INACTIVE"

    blocked = await api_client.post(
        f"/api/v1/agents/{agent['id']}/invoke",
        headers=admin_headers,
        json={"env": "DEV", "input": {}},
    )
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["error"]["code"] == "AGENT_INACTIVE"
    assert blocked.json()["error"]["details"][0]["current"] == "INACTIVE"
    assert await _run_count(tenant_env.tenant_id) == 0

    pointer = await api_client.get(
        f"/api/v1/agents/{agent['id']}/pointers/DEV", headers=admin_headers
    )
    assert pointer.status_code == 200, pointer.text

    started = await api_client.post(
        f"/api/v1/agents/{agent['id']}/activate", headers=admin_headers
    )
    assert started.status_code == 200, started.text
    assert started.json()["status"] == "ACTIVE"

    resumed = await api_client.post(
        f"/api/v1/agents/{agent['id']}/invoke",
        headers=admin_headers,
        json={"env": "DEV", "input": {}},
    )
    # 已越过状态门禁；模型不可达只会让本次 run 失败，不影响受理。
    assert resumed.status_code == 200, resumed.text
    assert await _run_count(tenant_env.tenant_id) == 1


async def test_publish_during_inactive_does_not_reactivate(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str]
) -> None:
    model = await create_model(api_client, admin_headers)
    prompt_version = await create_prompt_version(api_client, admin_headers)
    agent, first, _ = await _published_agent(api_client, admin_headers)

    stopped = await api_client.post(
        f"/api/v1/agents/{agent['id']}/deactivate", headers=admin_headers
    )
    assert stopped.status_code == 200, stopped.text

    second = await create_agent_version(
        api_client,
        admin_headers,
        agent["id"],
        model_id=model["id"],
        prompt_version_id=prompt_version["id"],
        policy={"max_steps": 5},
    )
    republished = await _publish(api_client, admin_headers, agent["id"], second["id"])
    assert republished.status_code == 201, republished.text

    fetched = await api_client.get(
        f"/api/v1/agents/{agent['id']}", headers=admin_headers
    )
    assert fetched.json()["status"] == "INACTIVE"

    pointer = await api_client.get(
        f"/api/v1/agents/{agent['id']}/pointers/DEV", headers=admin_headers
    )
    assert pointer.json()["agent_version_id"] == second["id"]
    assert pointer.json()["agent_version_id"] != first["id"]

    blocked = await api_client.post(
        f"/api/v1/agents/{agent['id']}/invoke",
        headers=admin_headers,
        json={"env": "DEV", "input": {}},
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "AGENT_INACTIVE"


async def test_activate_never_published_agent_is_rejected(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str]
) -> None:
    model = await create_model(api_client, admin_headers)
    prompt_version = await create_prompt_version(api_client, admin_headers)
    agent = await create_agent(api_client, admin_headers)
    await create_agent_version(
        api_client,
        admin_headers,
        agent["id"],
        model_id=model["id"],
        prompt_version_id=prompt_version["id"],
    )

    response = await api_client.post(
        f"/api/v1/agents/{agent['id']}/activate", headers=admin_headers
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "AGENT_NOT_PUBLISHED"

    fetched = await api_client.get(
        f"/api/v1/agents/{agent['id']}", headers=admin_headers
    )
    assert fetched.json()["status"] == "DRAFT"


async def test_archived_agent_is_terminal_and_closed_for_calls(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str]
) -> None:
    agent, version, release = await _published_agent(api_client, admin_headers)

    archived = await api_client.post(
        f"/api/v1/agents/{agent['id']}/archive", headers=admin_headers
    )
    assert archived.status_code == 200, archived.text
    assert archived.json()["status"] == "ARCHIVED"

    again = await api_client.post(
        f"/api/v1/agents/{agent['id']}/archive", headers=admin_headers
    )
    assert again.status_code == 200, again.text

    for action in ("activate", "deactivate"):
        rejected = await api_client.post(
            f"/api/v1/agents/{agent['id']}/{action}", headers=admin_headers
        )
        assert rejected.status_code == 409, rejected.text
        assert rejected.json()["error"]["code"] == "AGENT_STATUS_TRANSITION"

    invoke = await api_client.post(
        f"/api/v1/agents/{agent['id']}/invoke",
        headers=admin_headers,
        json={"env": "DEV", "input": {}},
    )
    assert invoke.status_code == 409
    assert invoke.json()["error"]["code"] == "AGENT_ARCHIVED"

    publish = await _publish(api_client, admin_headers, agent["id"], version["id"])
    assert publish.status_code == 409
    assert publish.json()["error"]["code"] == "AGENT_ARCHIVED"

    rollback = await api_client.post(
        f"/api/v1/agents/{agent['id']}/rollback",
        headers=admin_headers,
        json={"env": "DEV", "release_id": release["release_id"]},
    )
    assert rollback.status_code == 409
    assert rollback.json()["error"]["code"] == "AGENT_ARCHIVED"

    version_write = await api_client.post(
        f"/api/v1/agents/{agent['id']}/versions",
        headers=admin_headers,
        json={
            "model_id": version["snapshot"]["model"]["model_id"],
            "prompt_version_id": version["snapshot"]["prompt"]["prompt_version_id"],
        },
    )
    assert version_write.status_code == 409, version_write.text
    assert version_write.json()["error"]["code"] == "AGENT_ARCHIVED"

    copy = await api_client.post(
        f"/api/v1/agents/{agent['id']}/copy",
        headers=admin_headers,
        json={"code": f"archived-copy-{suffix()}"},
    )
    assert copy.status_code == 409, copy.text
    assert copy.json()["error"]["code"] == "AGENT_ARCHIVED"
    assert await _run_count(tenant_env.tenant_id) == 0


async def test_delete_empty_agent_frees_code(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str]
) -> None:
    code = f"empty-{suffix()}"
    created = await api_client.post(
        "/api/v1/agents",
        headers=admin_headers,
        json={"code": code, "name": "Empty"},
    )
    assert created.status_code == 201, created.text
    agent_id = created.json()["id"]

    removed = await api_client.delete(f"/api/v1/agents/{agent_id}", headers=admin_headers)
    assert removed.status_code == 204, removed.text

    gone = await api_client.get(f"/api/v1/agents/{agent_id}", headers=admin_headers)
    assert gone.status_code == 404

    listed = await api_client.get("/api/v1/agents", headers=admin_headers)
    assert listed.status_code == 200, listed.text
    assert all(item["id"] != agent_id for item in listed.json()["items"])

    again = await api_client.delete(f"/api/v1/agents/{agent_id}", headers=admin_headers)
    assert again.status_code == 404

    recreated = await api_client.post(
        "/api/v1/agents",
        headers=admin_headers,
        json={"code": code, "name": "Empty Again"},
    )
    assert recreated.status_code == 201, recreated.text


async def test_delete_agent_with_history_hides_it_but_keeps_rows(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str]
) -> None:
    agent, _, _ = await _published_agent(api_client, admin_headers)
    code = agent["code"]

    removed = await api_client.delete(f"/api/v1/agents/{agent['id']}", headers=admin_headers)
    assert removed.status_code == 204, removed.text

    gone = await api_client.get(f"/api/v1/agents/{agent['id']}", headers=admin_headers)
    assert gone.status_code == 404

    listed = await api_client.get("/api/v1/agents", headers=admin_headers)
    assert all(item["id"] != agent["id"] for item in listed.json()["items"])

    blocked = await api_client.post(
        f"/api/v1/agents/{agent['id']}/invoke",
        headers=admin_headers,
        json={"env": "DEV", "input": {}},
    )
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["error"]["code"] == "AGENT_DELETED"
    assert await _run_count(tenant_env.tenant_id) == 0

    # 历史行全部保留，只是被 deleted_at 隐藏
    async with session_scope(tenant_env.tenant_id) as session:
        row = (
            await session.execute(
                text(
                    "select deleted_at, status from agent.agent "
                    "where id = cast(:aid as uuid)"
                ),
                {"aid": agent["id"]},
            )
        ).one()
        versions = await session.scalar(
            text(
                "select count(*) from agent.agent_version where agent_id = cast(:aid as uuid)"
            ),
            {"aid": agent["id"]},
        )
        releases = await session.scalar(
            text(
                "select count(*) from agent.agent_release where agent_id = cast(:aid as uuid)"
            ),
            {"aid": agent["id"]},
        )
    assert row.deleted_at is not None
    assert row.status == "ACTIVE"  # 软删除不改状态
    assert versions == 1
    assert releases == 1

    # 编码释放，可以重新用同名编码建新 Agent
    recreated = await api_client.post(
        "/api/v1/agents", headers=admin_headers, json={"code": code, "name": "Reborn"}
    )
    assert recreated.status_code == 201, recreated.text
    assert recreated.json()["id"] != agent["id"]


async def test_lifecycle_writes_append_audit_events(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str]
) -> None:
    agent, _, _ = await _published_agent(api_client, admin_headers)
    await api_client.post(
        f"/api/v1/agents/{agent['id']}/deactivate", headers=admin_headers
    )
    await api_client.post(
        f"/api/v1/agents/{agent['id']}/activate", headers=admin_headers
    )
    copied = await api_client.post(
        f"/api/v1/agents/{agent['id']}/copy",
        headers=admin_headers,
        json={"code": f"audit-copy-{suffix()}"},
    )
    assert copied.status_code == 201, copied.text
    await api_client.post(
        f"/api/v1/agents/{agent['id']}/archive", headers=admin_headers
    )

    empty = await create_agent(api_client, admin_headers)
    deleted = await api_client.delete(
        f"/api/v1/agents/{empty['id']}", headers=admin_headers
    )
    assert deleted.status_code == 204, deleted.text

    async with session_scope(tenant_env.tenant_id) as session:
        rows = (
            await session.execute(
                text(
                    "select action, resource_id, detail from platform.audit_log "
                    "where resource_id = :rid order by created_at"
                ),
                {"rid": agent["id"]},
            )
        ).all()
    actions = [row.action for row in rows]
    assert "agent.deactivated" in actions
    assert "agent.activated" in actions
    assert "agent.archived" in actions

    async with session_scope(tenant_env.tenant_id) as session:
        copied_rows = (
            await session.execute(
                text(
                    "select action, detail from platform.audit_log "
                    "where resource_id = :rid"
                ),
                {"rid": copied.json()["id"]},
            )
        ).all()
    assert [row.action for row in copied_rows] == ["agent.copied"]
    assert copied_rows[0].detail["source_agent_id"] == agent["id"]
    assert copied_rows[0].detail["source_version_no"] == 1

    async with session_scope(tenant_env.tenant_id) as session:
        deleted_rows = (
            await session.execute(
                text(
                    "select action, detail from platform.audit_log "
                    "where resource_id = :rid"
                ),
                {"rid": empty["id"]},
            )
        ).all()
    assert [row.action for row in deleted_rows] == ["agent.deleted"]
    assert deleted_rows[0].detail["code"] == empty["code"]
