"""发布指针：发布、重复发布、回滚与发布前引用复检。"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from agent_platform.shared.db import session_scope, system_session_scope
from tests.integration.agent_helpers import (
    create_agent,
    create_agent_version,
    create_model,
    create_prompt_version,
    create_tool_version,
)
from tests.integration.conftest import TenantEnv


async def _publish(
    api_client,
    headers: dict[str, str],
    agent_id: str,
    version_id: str,
    env: str = "DEV",
) -> dict:
    response = await api_client.post(
        f"/api/v1/agents/{agent_id}/releases",
        headers=headers,
        json={"agent_version_id": version_id, "env": env},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _pointer(api_client, headers: dict[str, str], agent_id: str, env: str = "DEV") -> dict:
    response = await api_client.get(f"/api/v1/agents/{agent_id}/pointers/{env}", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


async def test_publish_moves_pointer_and_keeps_release_history(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str]
) -> None:
    model = await create_model(api_client, admin_headers)
    prompt_version = await create_prompt_version(api_client, admin_headers)
    agent = await create_agent(api_client, admin_headers)
    first = await create_agent_version(
        api_client,
        admin_headers,
        agent["id"],
        model_id=model["id"],
        prompt_version_id=prompt_version["id"],
    )
    second = await create_agent_version(
        api_client,
        admin_headers,
        agent["id"],
        model_id=model["id"],
        prompt_version_id=prompt_version["id"],
        policy={"max_steps": 4},
    )

    release_v1 = await _publish(api_client, admin_headers, agent["id"], first["id"])
    assert release_v1["version_no"] == 1
    assert (await _pointer(api_client, admin_headers, agent["id"]))["version_no"] == 1

    release_v2 = await _publish(api_client, admin_headers, agent["id"], second["id"])
    assert release_v2["version_no"] == 2
    assert (await _pointer(api_client, admin_headers, agent["id"]))["version_no"] == 2

    # 同一版本重复发布也产生新纪录，历史不被覆盖
    release_v1_again = await _publish(api_client, admin_headers, agent["id"], first["id"])
    assert release_v1_again["release_id"] not in {
        release_v1["release_id"],
        release_v2["release_id"],
    }
    history = await api_client.get(
        f"/api/v1/agents/{agent['id']}/releases", headers=admin_headers
    )
    assert history.status_code == 200, history.text
    assert len(history.json()) == 3
    assert {item["release_id"] for item in history.json()} == {
        release_v1["release_id"],
        release_v2["release_id"],
        release_v1_again["release_id"],
    }
    assert (await _pointer(api_client, admin_headers, agent["id"]))["version_no"] == 1

    # 环境之间互不影响
    await _publish(api_client, admin_headers, agent["id"], second["id"], env="TEST")
    assert (await _pointer(api_client, admin_headers, agent["id"], "DEV"))["version_no"] == 1
    assert (await _pointer(api_client, admin_headers, agent["id"], "TEST"))["version_no"] == 2
    dev_history = await api_client.get(
        f"/api/v1/agents/{agent['id']}/releases",
        headers=admin_headers,
        params={"env": "DEV"},
    )
    assert len(dev_history.json()) == 3

    # 发布记录同样不可修改
    with pytest.raises(DBAPIError):
        async with session_scope(tenant_env.tenant_id) as session:
            await session.execute(
                text("update agent.agent_release set status = 'ROLLED_BACK' where id = :rid"),
                {"rid": release_v1["release_id"]},
            )


async def test_rollback_switches_pointer_without_rewriting_history(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str]
) -> None:
    model = await create_model(api_client, admin_headers)
    prompt_version = await create_prompt_version(api_client, admin_headers)
    agent = await create_agent(api_client, admin_headers)
    first = await create_agent_version(
        api_client,
        admin_headers,
        agent["id"],
        model_id=model["id"],
        prompt_version_id=prompt_version["id"],
    )
    second = await create_agent_version(
        api_client,
        admin_headers,
        agent["id"],
        model_id=model["id"],
        prompt_version_id=prompt_version["id"],
        policy={"max_steps": 9},
    )
    release_v1 = await _publish(api_client, admin_headers, agent["id"], first["id"])
    release_v2 = await _publish(api_client, admin_headers, agent["id"], second["id"])

    rollback = await api_client.post(
        f"/api/v1/agents/{agent['id']}/rollback",
        headers=admin_headers,
        json={"env": "DEV", "release_id": release_v1["release_id"]},
    )
    assert rollback.status_code == 200, rollback.text
    assert rollback.json()["version_no"] == 1
    assert (await _pointer(api_client, admin_headers, agent["id"]))["version_no"] == 1

    history = await api_client.get(
        f"/api/v1/agents/{agent['id']}/releases", headers=admin_headers
    )
    assert len(history.json()) == 2
    assert {item["release_id"] for item in history.json()} == {
        release_v1["release_id"],
        release_v2["release_id"],
    }

    # 用其他环境的发布记录回滚应被拒绝
    test_release = await _publish(
        api_client, admin_headers, agent["id"], second["id"], env="TEST"
    )
    rejected = await api_client.post(
        f"/api/v1/agents/{agent['id']}/rollback",
        headers=admin_headers,
        json={"env": "DEV", "release_id": test_release["release_id"]},
    )
    assert rejected.status_code == 404


async def test_publish_rechecks_references_and_fails_closed(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str]
) -> None:
    # 1) 模型被停用
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
    disabled = await api_client.patch(
        f"/api/v1/models/{model['id']}/status",
        headers=admin_headers,
        json={"status": "DISABLED"},
    )
    assert disabled.status_code == 200, disabled.text

    rejected = await api_client.post(
        f"/api/v1/agents/{agent['id']}/releases",
        headers=admin_headers,
        json={"agent_version_id": version["id"], "env": "DEV"},
    )
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["error"]["code"] == "RESOURCE_UNAVAILABLE"
    assert rejected.json()["error"]["details"][0]["resource_id"] == model["id"]

    missing_pointer = await api_client.get(
        f"/api/v1/agents/{agent['id']}/pointers/DEV", headers=admin_headers
    )
    assert missing_pointer.status_code == 404
    releases = await api_client.get(
        f"/api/v1/agents/{agent['id']}/releases", headers=admin_headers
    )
    assert releases.json() == []

    # 2) 工具授权被撤销
    active_model = await create_model(api_client, admin_headers)
    prompt_for_tool = await create_prompt_version(api_client, admin_headers)
    tool_version = await create_tool_version(api_client, admin_headers)
    agent_with_tool = await create_agent(api_client, admin_headers)
    tool_bound = await create_agent_version(
        api_client,
        admin_headers,
        agent_with_tool["id"],
        model_id=active_model["id"],
        prompt_version_id=prompt_for_tool["id"],
        tool_version_ids=[tool_version["id"]],
    )
    revoked = await api_client.delete(
        f"/api/v1/tools/{tool_version['tool_id']}/grant", headers=admin_headers
    )
    assert revoked.status_code == 204, revoked.text

    tool_rejected = await api_client.post(
        f"/api/v1/agents/{agent_with_tool['id']}/releases",
        headers=admin_headers,
        json={"agent_version_id": tool_bound["id"], "env": "DEV"},
    )
    assert tool_rejected.status_code == 409, tool_rejected.text
    assert tool_rejected.json()["error"]["details"][0]["resource_id"] == tool_version["id"]

    # 3) Prompt 被归档
    model_for_prompt = await create_model(api_client, admin_headers)
    prompt_for_archive = await create_prompt_version(api_client, admin_headers)
    agent_for_prompt = await create_agent(api_client, admin_headers)
    prompt_bound = await create_agent_version(
        api_client,
        admin_headers,
        agent_for_prompt["id"],
        model_id=model_for_prompt["id"],
        prompt_version_id=prompt_for_archive["id"],
    )
    async with system_session_scope() as session:
        await session.execute(
            text("update catalog.prompt set status = 'ARCHIVED' where id = :pid"),
            {"pid": prompt_for_archive["prompt_id"]},
        )
    prompt_rejected = await api_client.post(
        f"/api/v1/agents/{agent_for_prompt['id']}/releases",
        headers=admin_headers,
        json={"agent_version_id": prompt_bound["id"], "env": "DEV"},
    )
    assert prompt_rejected.status_code == 409, prompt_rejected.text
    assert (
        prompt_rejected.json()["error"]["details"][0]["resource_id"]
        == prompt_for_archive["id"]
    )

    # 失败发布不产生发布记录，但要留审计
    async with system_session_scope() as session:
        failures = await session.scalar(
            text(
                "select count(*) from platform.audit_log where tenant_id = :tid "
                "and action = 'agent.release.published' and result = 'FAILURE'"
            ),
            {"tid": tenant_env.tenant_id},
        )
    assert failures == 3


async def test_release_of_other_tenant_is_invisible(
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
    missing = await api_client.post(
        f"/api/v1/agents/{uuid.uuid4()}/releases",
        headers=admin_headers,
        json={"agent_version_id": version["id"], "env": "DEV"},
    )
    assert missing.status_code == 404
