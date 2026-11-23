"""Agent 版本：快照冻结、config_hash 稳定、写事务原子性与版本对比。"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from agent_platform.contexts.agent.infrastructure.repositories import BindingRepository
from agent_platform.shared.db import session_scope
from tests.integration.agent_helpers import (
    add_prompt_version,
    add_tool_version,
    create_agent,
    create_agent_version,
    create_model,
    create_prompt_version,
    create_tool_version,
)
from tests.integration.conftest import TenantEnv


async def test_snapshot_is_frozen_and_hash_is_stable(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str]
) -> None:
    model = await create_model(api_client, admin_headers)
    prompt_version = await create_prompt_version(api_client, admin_headers)
    tool_version = await create_tool_version(api_client, admin_headers)
    agent = await create_agent(api_client, admin_headers)

    first = await create_agent_version(
        api_client,
        admin_headers,
        agent["id"],
        model_id=model["id"],
        prompt_version_id=prompt_version["id"],
        tool_version_ids=[tool_version["id"]],
        params={"temperature": 0.7},
        policy={"max_steps": 3, "timeout_s": 20, "token_budget": 2048},
    )
    assert first["version_no"] == 1
    assert first["snapshot"]["model"]["params"]["temperature"] == 0.7
    assert first["snapshot"]["prompt"]["messages"][0]["role"] == "system"
    assert first["snapshot"]["policy"] == {
        "max_steps": 3,
        "timeout_s": 20.0,
        "token_budget": 2048,
        "session_ttl_h": 168,
        "history_token_limit": 4000,
    }

    same_config = await create_agent_version(
        api_client,
        admin_headers,
        agent["id"],
        model_id=model["id"],
        prompt_version_id=prompt_version["id"],
        tool_version_ids=[tool_version["id"]],
        params={"temperature": 0.7},
        policy={"max_steps": 3, "timeout_s": 20, "token_budget": 2048},
    )
    assert same_config["version_no"] == 2
    assert same_config["config_hash"] == first["config_hash"]

    # 上游 Prompt 生成新版本后，旧快照内容与 hash 保持不变
    await add_prompt_version(api_client, admin_headers, prompt_version["prompt_id"])
    reloaded = await api_client.get(
        f"/api/v1/agent-versions/{first['id']}", headers=admin_headers
    )
    assert reloaded.status_code == 200, reloaded.text
    assert reloaded.json()["snapshot"]["prompt"]["version_no"] == 1
    assert reloaded.json()["snapshot"]["prompt"]["messages"] == first["snapshot"]["prompt"]["messages"]
    assert reloaded.json()["config_hash"] == first["config_hash"]


async def test_snapshot_rows_are_append_only(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str]
) -> None:
    model = await create_model(api_client, admin_headers)
    prompt_version = await create_prompt_version(api_client, admin_headers)
    agent = await create_agent(api_client, admin_headers)
    version = await create_agent_version(
        api_client,
        admin_headers,
        agent["id"],
        model_id=model["id"],
        prompt_version_id=prompt_version["id"],
    )

    with pytest.raises(DBAPIError):
        async with session_scope(tenant_env.tenant_id) as session:
            await session.execute(
                text("update agent.agent_version set note = 'tampered' where id = :vid"),
                {"vid": version["id"]},
            )


async def test_version_creation_rolls_back_when_binding_write_fails(
    api_client,
    tenant_env: TenantEnv,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
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

    async def broken_create_many(self, session, *, tenant_id, bindings):
        raise RuntimeError("simulated binding write failure")

    monkeypatch.setattr(BindingRepository, "create_many", broken_create_many)

    with pytest.raises(RuntimeError):
        await api_client.post(
            f"/api/v1/agents/{agent['id']}/versions",
            headers=admin_headers,
            json={"model_id": model["id"], "prompt_version_id": prompt_version["id"]},
        )

    versions = await api_client.get(
        f"/api/v1/agents/{agent['id']}/versions", headers=admin_headers
    )
    assert [item["version_no"] for item in versions.json()] == [1]


async def test_version_diff_reports_bindings_and_policy_changes(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str]
) -> None:
    model = await create_model(api_client, admin_headers)
    prompt_version = await create_prompt_version(api_client, admin_headers)
    tool_version = await create_tool_version(api_client, admin_headers)
    agent = await create_agent(api_client, admin_headers)

    base = await create_agent_version(
        api_client,
        admin_headers,
        agent["id"],
        model_id=model["id"],
        prompt_version_id=prompt_version["id"],
        tool_version_ids=[tool_version["id"]],
        params={"temperature": 0.2},
        policy={"max_steps": 6},
    )

    prompt_v2 = await add_prompt_version(api_client, admin_headers, prompt_version["prompt_id"])
    tool_v2 = await add_tool_version(api_client, admin_headers, tool_version["tool_id"])
    target = await create_agent_version(
        api_client,
        admin_headers,
        agent["id"],
        model_id=model["id"],
        prompt_version_id=prompt_v2["id"],
        tool_version_ids=[tool_v2["id"]],
        params={"temperature": 1.0},
        policy={"max_steps": 8},
    )

    diff = await api_client.get(
        "/api/v1/agent-versions/diff",
        headers=admin_headers,
        params={"base_version_id": base["id"], "target_version_id": target["id"]},
    )
    assert diff.status_code == 200, diff.text
    body = diff.json()
    assert body["identical"] is False
    assert body["config_hash_changed"] is True
    assert body["model"]["changed"] is True
    assert body["prompt"]["changed"] is True
    assert body["prompt"]["base"]["version_no"] == 1
    assert body["prompt"]["target"]["version_no"] == 2
    assert body["tools"]["changed"][0]["tool_code"] == tool_version["tool_code"]
    assert body["policy"]["max_steps"] == {"base": 6, "target": 8}

    same = await api_client.get(
        "/api/v1/agent-versions/diff",
        headers=admin_headers,
        params={"base_version_id": base["id"], "target_version_id": base["id"]},
    )
    assert same.status_code == 200, same.text
    assert same.json()["identical"] is True
    assert same.json()["policy"] == {}
