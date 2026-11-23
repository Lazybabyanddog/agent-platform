"""Agent 控制台：分页列表、筛选、详情聚合与发布体检。"""

from __future__ import annotations

from typing import Any

import httpx
from sqlalchemy import event, text

from agent_platform.shared.db import get_engine, session_scope
from tests.integration.agent_helpers import (
    create_agent,
    create_agent_version,
    create_model,
    create_prompt_version,
    create_tool_version,
    suffix,
)
from tests.integration.conftest import TenantEnv


async def _named_agent(
    api_client: httpx.AsyncClient, headers: dict[str, str], name: str, *, tags: list[str] | None = None
) -> dict[str, Any]:
    response = await api_client.post(
        "/api/v1/agents",
        headers=headers,
        json={
            "code": f"{name.lower().replace(' ', '-')}-{suffix()}",
            "name": name,
            "tags": tags or [],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_list_is_paginated_and_reports_total(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str]
) -> None:
    for index in range(3):
        await _named_agent(api_client, admin_headers, f"Paged {index}")

    first = await api_client.get(
        "/api/v1/agents", headers=admin_headers, params={"page": 1, "page_size": 2}
    )
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["total"] == 3
    assert body["page"] == 1
    assert body["page_size"] == 2
    assert len(body["items"]) == 2
    assert all("environments" in item for item in body["items"])

    second = await api_client.get(
        "/api/v1/agents", headers=admin_headers, params={"page": 2, "page_size": 2}
    )
    assert len(second.json()["items"]) == 1

    beyond = await api_client.get(
        "/api/v1/agents", headers=admin_headers, params={"page": 9, "page_size": 2}
    )
    assert beyond.status_code == 200
    assert beyond.json()["items"] == []
    assert beyond.json()["total"] == 3


async def test_list_rejects_invalid_pagination(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str]
) -> None:
    for params in ({"page": 0}, {"page_size": 0}, {"page_size": 101}):
        response = await api_client.get("/api/v1/agents", headers=admin_headers, params=params)
        assert response.status_code == 422, (params, response.text)


async def test_list_filters_by_status_type_tag_and_keyword(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str]
) -> None:
    model = await create_model(api_client, admin_headers)
    prompt_version = await create_prompt_version(api_client, admin_headers)

    draft = await _named_agent(
        api_client, admin_headers, "Draft One", tags=["support", "beta"]
    )
    published = await _named_agent(api_client, admin_headers, "Live Two", tags=["support"])
    version = await create_agent_version(
        api_client,
        admin_headers,
        published["id"],
        model_id=model["id"],
        prompt_version_id=prompt_version["id"],
    )
    released = await api_client.post(
        f"/api/v1/agents/{published['id']}/releases",
        headers=admin_headers,
        json={"agent_version_id": version["id"], "env": "DEV"},
    )
    assert released.status_code == 201, released.text

    by_status = await api_client.get(
        "/api/v1/agents", headers=admin_headers, params={"status": "ACTIVE"}
    )
    assert [item["id"] for item in by_status.json()["items"]] == [published["id"]]
    assert by_status.json()["total"] == 1

    by_status_draft = await api_client.get(
        "/api/v1/agents", headers=admin_headers, params={"status": "DRAFT"}
    )
    assert [item["id"] for item in by_status_draft.json()["items"]] == [draft["id"]]

    by_tag = await api_client.get(
        "/api/v1/agents", headers=admin_headers, params={"tag": "beta"}
    )
    assert [item["id"] for item in by_tag.json()["items"]] == [draft["id"]]

    by_type = await api_client.get(
        "/api/v1/agents", headers=admin_headers, params={"type": "CHAT"}
    )
    assert by_type.json()["total"] == 2

    by_keyword = await api_client.get(
        "/api/v1/agents", headers=admin_headers, params={"q": "live"}
    )
    assert [item["id"] for item in by_keyword.json()["items"]] == [published["id"]]


async def test_list_orders_by_recent_update(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str]
) -> None:
    oldest = await _named_agent(api_client, admin_headers, "Oldest")
    newest = await _named_agent(api_client, admin_headers, "Newest")

    response = await api_client.get("/api/v1/agents", headers=admin_headers)
    codes = [item["id"] for item in response.json()["items"]]
    assert codes.index(newest["id"]) < codes.index(oldest["id"])


async def test_list_does_not_query_pointers_per_row(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str]
) -> None:
    for index in range(4):
        await _named_agent(api_client, admin_headers, f"Batch {index}")

    engine = get_engine()
    statements: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", record)
    try:
        response = await api_client.get(
            "/api/v1/agents", headers=admin_headers, params={"page_size": 10}
        )
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", record)
    assert response.status_code == 200, response.text

    agent_queries = [s for s in statements if "FROM agent.agent " in s]
    pointer_queries = [s for s in statements if "FROM agent.agent_release_pointer" in s]
    assert len(pointer_queries) == 1
    assert len(agent_queries) == 2


async def test_detail_reports_three_environment_slots(
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
    released = await api_client.post(
        f"/api/v1/agents/{agent['id']}/releases",
        headers=admin_headers,
        json={"agent_version_id": version["id"], "env": "DEV"},
    )
    assert released.status_code == 201, released.text

    detail = await api_client.get(
        f"/api/v1/agents/{agent['id']}", headers=admin_headers
    )
    assert detail.status_code == 200, detail.text
    environments = detail.json()["environments"]
    assert set(environments) == {"DEV", "TEST", "PROD"}
    assert environments["DEV"]["agent_version_id"] == version["id"]
    assert environments["DEV"]["version_no"] == 1
    assert environments["TEST"] is None
    assert environments["PROD"] is None

    listed = await api_client.get(
        "/api/v1/agents", headers=admin_headers, params={"q": agent["code"]}
    )
    listed_envs = listed.json()["items"][0]["environments"]
    assert listed_envs["DEV"]["version_no"] == 1
    assert listed_envs["TEST"] is None


async def test_readiness_lists_every_binding(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str]
) -> None:
    model = await create_model(api_client, admin_headers)
    prompt_version = await create_prompt_version(api_client, admin_headers)
    tool_version = await create_tool_version(api_client, admin_headers)
    agent = await create_agent(api_client, admin_headers)
    version = await create_agent_version(
        api_client,
        admin_headers,
        agent["id"],
        model_id=model["id"],
        prompt_version_id=prompt_version["id"],
        tool_version_ids=[tool_version["id"]],
    )

    response = await api_client.get(
        f"/api/v1/agents/{agent['id']}/versions/{version['id']}/readiness",
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ready"] is True
    assert body["config_hash"] == version["config_hash"]
    assert body["preview"] is None
    assert [item["role"] for item in body["bindings"]] == ["MODEL", "PROMPT", "TOOL"]
    assert all(item["available"] for item in body["bindings"])


async def test_readiness_marks_disabled_reference(
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
    disabled = await api_client.patch(
        f"/api/v1/models/{model['id']}/status",
        headers=admin_headers,
        json={"status": "DISABLED"},
    )
    assert disabled.status_code == 200, disabled.text

    response = await api_client.get(
        f"/api/v1/agents/{agent['id']}/versions/{version['id']}/readiness",
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ready"] is False
    model_binding = body["bindings"][0]
    assert model_binding["available"] is False
    assert model_binding["reason"] == "model is disabled"
    assert body["bindings"][1]["available"] is True


async def test_readiness_marks_revoked_tool_grant(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str]
) -> None:
    model = await create_model(api_client, admin_headers)
    prompt_version = await create_prompt_version(api_client, admin_headers)
    tool_version = await create_tool_version(api_client, admin_headers)
    agent = await create_agent(api_client, admin_headers)
    version = await create_agent_version(
        api_client,
        admin_headers,
        agent["id"],
        model_id=model["id"],
        prompt_version_id=prompt_version["id"],
        tool_version_ids=[tool_version["id"]],
    )

    async with session_scope(tenant_env.tenant_id) as session:
        await session.execute(
            text("delete from catalog.tool_grant where tool_id = :tid"),
            {"tid": tool_version["tool_id"]},
        )

    response = await api_client.get(
        f"/api/v1/agents/{agent['id']}/versions/{version['id']}/readiness",
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ready"] is False
    tool_binding = next(item for item in body["bindings"] if item["role"] == "TOOL")
    assert tool_binding["available"] is False
    assert tool_binding["reason"] == "tool is not granted"

    blocked = await api_client.post(
        f"/api/v1/agents/{agent['id']}/releases",
        headers=admin_headers,
        json={"agent_version_id": version["id"], "env": "DEV"},
    )
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["error"]["code"] == "RESOURCE_UNAVAILABLE"


async def test_readiness_is_read_only(
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
    released = await api_client.post(
        f"/api/v1/agents/{agent['id']}/releases",
        headers=admin_headers,
        json={"agent_version_id": version["id"], "env": "DEV"},
    )
    assert released.status_code == 201, released.text

    before = await api_client.get(
        f"/api/v1/agents/{agent['id']}/pointers/DEV", headers=admin_headers
    )
    for _ in range(2):
        checked = await api_client.get(
            f"/api/v1/agents/{agent['id']}/versions/{version['id']}/readiness",
            headers=admin_headers,
        )
        assert checked.status_code == 200, checked.text

    after = await api_client.get(
        f"/api/v1/agents/{agent['id']}/pointers/DEV", headers=admin_headers
    )
    assert before.json() == after.json()
    releases = await api_client.get(
        f"/api/v1/agents/{agent['id']}/releases",
        headers=admin_headers,
        params={"env": "DEV"},
    )
    assert len(releases.json()) == 1


async def test_readiness_unknown_version_is_404(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str]
) -> None:
    agent = await create_agent(api_client, admin_headers)
    other = await create_agent(api_client, admin_headers)
    model = await create_model(api_client, admin_headers)
    prompt_version = await create_prompt_version(api_client, admin_headers)
    version = await create_agent_version(
        api_client,
        admin_headers,
        other["id"],
        model_id=model["id"],
        prompt_version_id=prompt_version["id"],
    )

    response = await api_client.get(
        f"/api/v1/agents/{agent['id']}/versions/{version['id']}/readiness",
        headers=admin_headers,
    )
    assert response.status_code == 404, response.text
