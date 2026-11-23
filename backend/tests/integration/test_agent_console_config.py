"""Agent 控制台：带初始配置创建、复制与基准版本冲突。"""

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
    create_tool_version,
    suffix,
)
from tests.integration.conftest import TenantEnv


async def _create_agent_with_config(
    api_client: httpx.AsyncClient,
    headers: dict[str, str],
    *,
    code: str,
    config: dict[str, Any],
) -> httpx.Response:
    return await api_client.post(
        "/api/v1/agents",
        headers=headers,
        json={"code": code, "name": "Configured Agent", "config": config},
    )


async def test_create_agent_with_config_generates_version_one(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str]
) -> None:
    model = await create_model(api_client, admin_headers)
    prompt_version = await create_prompt_version(api_client, admin_headers)
    tool_version = await create_tool_version(api_client, admin_headers)

    response = await _create_agent_with_config(
        api_client,
        admin_headers,
        code=f"configured-{suffix()}",
        config={
            "model_id": model["id"],
            "prompt_version_id": prompt_version["id"],
            "tool_version_ids": [tool_version["id"]],
            "params": {"temperature": 0.4},
            "policy": {"max_steps": 4},
        },
    )
    assert response.status_code == 201, response.text
    agent = response.json()
    assert agent["status"] == "DRAFT"

    versions = await api_client.get(
        f"/api/v1/agents/{agent['id']}/versions", headers=admin_headers
    )
    assert versions.status_code == 200, versions.text
    body = versions.json()
    assert [item["version_no"] for item in body] == [1]
    assert body[0]["note"] == "initial config"
    assert body[0]["snapshot"]["model"]["params"]["temperature"] == 0.4
    assert body[0]["snapshot"]["prompt"]["prompt_version_id"] == prompt_version["id"]
    assert body[0]["snapshot"]["tools"][0]["tool_version_id"] == tool_version["id"]
    assert body[0]["snapshot"]["policy"]["max_steps"] == 4


async def test_create_agent_rolls_back_when_reference_unavailable(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str]
) -> None:
    model = await create_model(api_client, admin_headers)
    prompt_version = await create_prompt_version(api_client, admin_headers)
    disabled = await api_client.patch(
        f"/api/v1/models/{model['id']}/status",
        headers=admin_headers,
        json={"status": "DISABLED"},
    )
    assert disabled.status_code == 200, disabled.text

    code = f"broken-{suffix()}"
    response = await _create_agent_with_config(
        api_client,
        admin_headers,
        code=code,
        config={"model_id": model["id"], "prompt_version_id": prompt_version["id"]},
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "RESOURCE_UNAVAILABLE"

    agents = await api_client.get("/api/v1/agents", headers=admin_headers)
    assert agents.status_code == 200, agents.text
    assert all(item["code"] != code for item in agents.json()["items"])


async def test_create_version_rejects_stale_base_version(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str]
) -> None:
    model = await create_model(api_client, admin_headers)
    prompt_version = await create_prompt_version(api_client, admin_headers)
    agent = await create_agent(api_client, admin_headers)

    v1 = await create_agent_version(
        api_client,
        admin_headers,
        agent["id"],
        model_id=model["id"],
        prompt_version_id=prompt_version["id"],
        policy={"max_steps": 3},
    )
    v2 = await create_agent_version(
        api_client,
        admin_headers,
        agent["id"],
        model_id=model["id"],
        prompt_version_id=prompt_version["id"],
        policy={"max_steps": 5},
    )

    stale = await api_client.post(
        f"/api/v1/agents/{agent['id']}/versions",
        headers=admin_headers,
        json={
            "model_id": model["id"],
            "prompt_version_id": prompt_version["id"],
            "policy": {"max_steps": 7},
            "base_version_id": v1["id"],
        },
    )
    assert stale.status_code == 409, stale.text
    error = stale.json()["error"]
    assert error["code"] == "AGENT_VERSION_CONFLICT"
    detail = error["details"][0]
    assert detail["base_version_no"] == 1
    assert detail["latest_version_no"] == 2
    assert detail["latest_version_id"] == v2["id"]
    assert detail["diff"]["policy"]["max_steps"] == {"base": 3, "target": 5}

    accepted = await api_client.post(
        f"/api/v1/agents/{agent['id']}/versions",
        headers=admin_headers,
        json={
            "model_id": model["id"],
            "prompt_version_id": prompt_version["id"],
            "policy": {"max_steps": 7},
            "base_version_id": v2["id"],
        },
    )
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["version_no"] == 3


async def test_create_version_rejects_base_from_other_agent(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str]
) -> None:
    model = await create_model(api_client, admin_headers)
    prompt_version = await create_prompt_version(api_client, admin_headers)
    first = await create_agent(api_client, admin_headers)
    second = await create_agent(api_client, admin_headers)
    foreign = await create_agent_version(
        api_client,
        admin_headers,
        second["id"],
        model_id=model["id"],
        prompt_version_id=prompt_version["id"],
    )

    response = await api_client.post(
        f"/api/v1/agents/{first['id']}/versions",
        headers=admin_headers,
        json={
            "model_id": model["id"],
            "prompt_version_id": prompt_version["id"],
            "base_version_id": foreign["id"],
        },
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "VALIDATION_FAILED"


async def test_copy_agent_reuses_latest_snapshot(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str]
) -> None:
    model = await create_model(api_client, admin_headers)
    prompt_version = await create_prompt_version(api_client, admin_headers)
    tool_version = await create_tool_version(api_client, admin_headers)
    agent = await create_agent(api_client, admin_headers)
    await create_agent_version(
        api_client,
        admin_headers,
        agent["id"],
        model_id=model["id"],
        prompt_version_id=prompt_version["id"],
        policy={"max_steps": 3},
    )
    latest = await create_agent_version(
        api_client,
        admin_headers,
        agent["id"],
        model_id=model["id"],
        prompt_version_id=prompt_version["id"],
        tool_version_ids=[tool_version["id"]],
        params={"temperature": 0.9},
        policy={"max_steps": 9, "session_ttl_h": 24},
    )

    code = f"copy-{suffix()}"
    copied = await api_client.post(
        f"/api/v1/agents/{agent['id']}/copy",
        headers=admin_headers,
        json={"code": code},
    )
    assert copied.status_code == 201, copied.text
    new_agent = copied.json()
    assert new_agent["code"] == code
    assert new_agent["status"] == "DRAFT"
    assert new_agent["name"] == f"{agent['name']} (copy)"

    versions = await api_client.get(
        f"/api/v1/agents/{new_agent['id']}/versions", headers=admin_headers
    )
    assert versions.status_code == 200, versions.text
    body = versions.json()
    assert [item["version_no"] for item in body] == [1]
    assert body[0]["snapshot"] == latest["snapshot"]
    assert body[0]["config_hash"] == latest["config_hash"]

    for env in ("DEV", "TEST", "PROD"):
        releases = await api_client.get(
            f"/api/v1/agents/{new_agent['id']}/releases",
            headers=admin_headers,
            params={"env": env},
        )
        assert releases.status_code == 200, releases.text
        assert releases.json() == []
        pointer = await api_client.get(
            f"/api/v1/agents/{new_agent['id']}/pointers/{env}", headers=admin_headers
        )
        assert pointer.status_code == 404


async def test_copy_empty_agent_only_creates_identity(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str]
) -> None:
    agent = await create_agent(api_client, admin_headers)
    copied = await api_client.post(
        f"/api/v1/agents/{agent['id']}/copy",
        headers=admin_headers,
        json={"code": f"copy-{suffix()}"},
    )
    assert copied.status_code == 201, copied.text
    versions = await api_client.get(
        f"/api/v1/agents/{copied.json()['id']}/versions", headers=admin_headers
    )
    assert versions.status_code == 200, versions.text
    assert versions.json() == []


async def test_copy_rejects_existing_code(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str]
) -> None:
    model = await create_model(api_client, admin_headers)
    prompt_version = await create_prompt_version(api_client, admin_headers)
    source = await create_agent(api_client, admin_headers)
    await create_agent_version(
        api_client,
        admin_headers,
        source["id"],
        model_id=model["id"],
        prompt_version_id=prompt_version["id"],
    )

    response = await api_client.post(
        f"/api/v1/agents/{source['id']}/copy",
        headers=admin_headers,
        json={"code": source["code"]},
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "CONFLICT"


async def test_archived_agent_cannot_create_version(
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

    async with session_scope(tenant_env.tenant_id) as session:
        await session.execute(
            text("update agent.agent set status = 'ARCHIVED' where id = :aid"),
            {"aid": agent["id"]},
        )

    response = await api_client.post(
        f"/api/v1/agents/{agent['id']}/versions",
        headers=admin_headers,
        json={"model_id": model["id"], "prompt_version_id": prompt_version["id"]},
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "AGENT_ARCHIVED"
