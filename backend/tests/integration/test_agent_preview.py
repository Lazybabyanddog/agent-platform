"""调试预览：PREVIEW 运行、只读装配与权限边界。"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from agent_platform.shared.db import session_scope, system_session_scope
from tests.integration.agent_helpers import (
    create_agent,
    create_model,
    create_prompt_version,
)
from tests.integration.conftest import TenantEnv
from tests.integration.test_agent_invoke import StaticModel, use_model


def _preview_payload(model_id: str, prompt_version_id: str, **overrides) -> dict:
    payload = {
        "model_id": model_id,
        "prompt_version_id": prompt_version_id,
        "tool_version_ids": [],
        "params": {"temperature": 0.2},
        "policy": {"max_steps": 4, "session_ttl_h": 48},
        "input": {},
    }
    payload.update(overrides)
    return payload


async def _run_rows(tenant_id) -> list:
    async with session_scope(tenant_id) as session:
        return (
            await session.execute(
                text(
                    "select kind, config_hash, preview_snapshot, agent_release_id, "
                    "agent_version_id, session_id from execution.run"
                )
            )
        ).all()


async def test_preview_runs_without_creating_version_or_pointer(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str], monkeypatch
) -> None:
    use_model(monkeypatch, StaticModel(content="preview says hi"))
    model = await create_model(api_client, admin_headers)
    prompt_version = await create_prompt_version(api_client, admin_headers)
    agent = await create_agent(api_client, admin_headers)

    response = await api_client.post(
        f"/api/v1/agents/{agent['id']}/preview",
        headers=admin_headers,
        json=_preview_payload(model["id"], prompt_version["id"]),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "SUCCESS"
    assert body["output"] == "preview says hi"
    assert body["config_hash"]
    assert body["preview_snapshot"]["policy"]["session_ttl_h"] == 48
    assert response.headers["X-Trace-Id"] == body["trace_id"]

    rows = await _run_rows(tenant_env.tenant_id)
    assert len(rows) == 1
    row = rows[0]
    assert row.kind == "PREVIEW"
    assert row.config_hash == body["config_hash"]
    assert row.preview_snapshot is not None
    assert row.agent_release_id is None
    assert row.agent_version_id is None

    versions = await api_client.get(
        f"/api/v1/agents/{agent['id']}/versions", headers=admin_headers
    )
    assert versions.json() == []
    pointer = await api_client.get(
        f"/api/v1/agents/{agent['id']}/pointers/DEV", headers=admin_headers
    )
    assert pointer.status_code == 404


async def test_preview_and_saved_version_share_config_hash(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str], monkeypatch
) -> None:
    use_model(monkeypatch, StaticModel())
    model = await create_model(api_client, admin_headers)
    prompt_version = await create_prompt_version(api_client, admin_headers)
    agent = await create_agent(api_client, admin_headers)
    payload = _preview_payload(model["id"], prompt_version["id"])

    previewed = await api_client.post(
        f"/api/v1/agents/{agent['id']}/preview",
        headers=admin_headers,
        json=payload,
    )
    assert previewed.status_code == 200, previewed.text

    saved = await api_client.post(
        f"/api/v1/agents/{agent['id']}/versions",
        headers=admin_headers,
        json={
            "model_id": payload["model_id"],
            "prompt_version_id": payload["prompt_version_id"],
            "tool_version_ids": payload["tool_version_ids"],
            "params": payload["params"],
            "policy": payload["policy"],
        },
    )
    assert saved.status_code == 201, saved.text
    assert saved.json()["config_hash"] == previewed.json()["config_hash"]

    readiness = await api_client.get(
        f"/api/v1/agents/{agent['id']}/versions/{saved.json()['id']}/readiness",
        headers=admin_headers,
    )
    assert readiness.json()["preview"]["run_id"] == previewed.json()["run_id"]
    assert readiness.json()["preview"]["status"] == "SUCCESS"


async def test_preview_rejects_unavailable_reference_without_run(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str]
) -> None:
    model = await create_model(api_client, admin_headers)
    prompt_version = await create_prompt_version(api_client, admin_headers)
    agent = await create_agent(api_client, admin_headers)
    disabled = await api_client.patch(
        f"/api/v1/models/{model['id']}/status",
        headers=admin_headers,
        json={"status": "DISABLED"},
    )
    assert disabled.status_code == 200, disabled.text

    response = await api_client.post(
        f"/api/v1/agents/{agent['id']}/preview",
        headers=admin_headers,
        json=_preview_payload(model["id"], prompt_version["id"]),
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "RESOURCE_UNAVAILABLE"
    assert await _run_rows(tenant_env.tenant_id) == []


async def test_preview_rejects_archived_agent(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str]
) -> None:
    model = await create_model(api_client, admin_headers)
    prompt_version = await create_prompt_version(api_client, admin_headers)
    agent = await create_agent(api_client, admin_headers)
    archived = await api_client.post(
        f"/api/v1/agents/{agent['id']}/archive", headers=admin_headers
    )
    assert archived.status_code == 200, archived.text

    response = await api_client.post(
        f"/api/v1/agents/{agent['id']}/preview",
        headers=admin_headers,
        json=_preview_payload(model["id"], prompt_version["id"]),
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "AGENT_ARCHIVED"
    assert await _run_rows(tenant_env.tenant_id) == []


async def test_preview_reuses_policy_validation(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str]
) -> None:
    model = await create_model(api_client, admin_headers)
    prompt_version = await create_prompt_version(api_client, admin_headers)
    agent = await create_agent(api_client, admin_headers)

    response = await api_client.post(
        f"/api/v1/agents/{agent['id']}/preview",
        headers=admin_headers,
        json=_preview_payload(
            model["id"], prompt_version["id"], policy={"max_steps": 999}
        ),
    )
    assert response.status_code == 422, response.text
    assert await _run_rows(tenant_env.tenant_id) == []


async def test_member_can_preview_but_cannot_save(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str], monkeypatch
) -> None:
    use_model(monkeypatch, StaticModel())
    model = await create_model(api_client, admin_headers)
    prompt_version = await create_prompt_version(api_client, admin_headers)
    agent = await create_agent(api_client, admin_headers)

    email = f"preview-member-{uuid.uuid4().hex[:8]}@example.com"
    password = "MemberPass123!"
    registered = await api_client.post(
        "/api/v1/auth/register",
        json={
            "tenant_code": tenant_env.tenant_code,
            "email": email,
            "password": password,
            "display_name": "Preview Member",
        },
    )
    assert registered.status_code == 201, registered.text
    try:
        login = await api_client.post(
            "/api/v1/auth/login",
            json={"tenant_code": tenant_env.tenant_code, "email": email, "password": password},
        )
        assert login.status_code == 200, login.text
        member_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        previewed = await api_client.post(
            f"/api/v1/agents/{agent['id']}/preview",
            headers=member_headers,
            json=_preview_payload(model["id"], prompt_version["id"]),
        )
        assert previewed.status_code == 200, previewed.text

        blocked = await api_client.post(
            f"/api/v1/agents/{agent['id']}/versions",
            headers=member_headers,
            json={"model_id": model["id"], "prompt_version_id": prompt_version["id"]},
        )
        assert blocked.status_code == 403, blocked.text
    finally:
        user_id = registered.json()["user_id"]
        async with system_session_scope() as session:
            await session.execute(
                text("delete from platform.membership where user_id = :uid"), {"uid": user_id}
            )
            await session.execute(
                text("delete from platform.user_role where user_id = :uid"), {"uid": user_id}
            )
            await session.execute(
                text("delete from platform.user_account where id = :uid"), {"uid": user_id}
            )


@pytest.mark.parametrize("timeout_s", [0, -1])
async def test_preview_rejects_non_positive_timeout(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str], timeout_s: float
) -> None:
    model = await create_model(api_client, admin_headers)
    prompt_version = await create_prompt_version(api_client, admin_headers)
    agent = await create_agent(api_client, admin_headers)

    response = await api_client.post(
        f"/api/v1/agents/{agent['id']}/preview",
        headers=admin_headers,
        json=_preview_payload(
            model["id"], prompt_version["id"], timeout_s=timeout_s
        ),
    )
    assert response.status_code == 422, response.text
