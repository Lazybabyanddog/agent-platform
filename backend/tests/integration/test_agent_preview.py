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


async def _qa_prompt_version(api_client, headers: dict[str, str]) -> dict:
    """带用户消息模板的提示词：让模型消息序列里有用户输入，便于断言插入位置。"""
    created = await api_client.post(
        "/api/v1/prompts",
        headers=headers,
        json={
            "code": f"qa-{uuid.uuid4().hex[:8]}",
            "name": "QA",
            "messages": [
                {"role": "system", "content": "你是助手"},
                {"role": "user", "content": "Q: {{q}}"},
            ],
            "variables": [{"name": "q", "type": "string", "required": True}],
        },
    )
    assert created.status_code == 201, created.text
    versions = await api_client.get(
        f"/api/v1/prompts/{created.json()['id']}/versions", headers=headers
    )
    assert versions.status_code == 200, versions.text
    return versions.json()[0]


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


async def test_preview_injects_declared_runtime_context(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str], monkeypatch
) -> None:
    model_impl = StaticModel()
    use_model(monkeypatch, model_impl)
    model = await create_model(api_client, admin_headers)
    prompt_version = await _qa_prompt_version(api_client, admin_headers)
    agent = await create_agent(api_client, admin_headers)
    payload = _preview_payload(
        model["id"],
        prompt_version["id"],
        runtime_context=["now", "timezone"],
        input={"q": "1"},
    )

    response = await api_client.post(
        f"/api/v1/agents/{agent['id']}/preview",
        headers=admin_headers,
        json=payload,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["preview_snapshot"]["runtime_context"] == ["now", "timezone"]

    # 上下文插在系统消息之后、用户输入之前，且模板系统消息保持不变
    messages = model_impl.requests[0].messages
    assert messages[0].role == "system"
    assert "当前时间：" in messages[1].content
    assert "时区：" in messages[1].content
    assert messages[2].role == "user"

    rows = await _run_rows(tenant_env.tenant_id)
    assert rows[0].preview_snapshot["runtime_context"] == ["now", "timezone"]

    # 与保存版本共用同一套装配：同一组引用与声明得到相同 config_hash
    saved = await api_client.post(
        f"/api/v1/agents/{agent['id']}/versions",
        headers=admin_headers,
        json={
            "model_id": payload["model_id"],
            "prompt_version_id": payload["prompt_version_id"],
            "tool_version_ids": payload["tool_version_ids"],
            "params": payload["params"],
            "policy": payload["policy"],
            "runtime_context": payload["runtime_context"],
        },
    )
    assert saved.status_code == 201, saved.text
    assert saved.json()["config_hash"] == body["config_hash"]
    assert saved.json()["snapshot"]["runtime_context"] == ["now", "timezone"]

    undeclared = await api_client.post(
        f"/api/v1/agents/{agent['id']}/preview",
        headers=admin_headers,
        json=_preview_payload(model["id"], prompt_version["id"], input={"q": "1"}),
    )
    assert undeclared.status_code == 200, undeclared.text
    assert undeclared.json()["preview_snapshot"]["runtime_context"] == []
    assert undeclared.json()["config_hash"] != body["config_hash"]


async def test_preview_rejects_unknown_runtime_context_item(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str]
) -> None:
    model = await create_model(api_client, admin_headers)
    prompt_version = await create_prompt_version(api_client, admin_headers)
    agent = await create_agent(api_client, admin_headers)

    response = await api_client.post(
        f"/api/v1/agents/{agent['id']}/preview",
        headers=admin_headers,
        json=_preview_payload(
            model["id"], prompt_version["id"], runtime_context=["now", "moon_phase"]
        ),
    )
    assert response.status_code == 422, response.text
    detail = response.json()["error"]["details"][0]
    assert detail["field"] == "runtime_context"
    assert "moon_phase" in detail["reason"]
    assert await _run_rows(tenant_env.tenant_id) == []


async def test_preview_runtime_context_not_accumulated_in_session(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str], monkeypatch
) -> None:
    model_impl = StaticModel(content="ok")
    use_model(monkeypatch, model_impl)
    model = await create_model(api_client, admin_headers)
    prompt_version = await _qa_prompt_version(api_client, admin_headers)
    agent = await create_agent(api_client, admin_headers)
    payload = _preview_payload(
        model["id"],
        prompt_version["id"],
        runtime_context=["now", "timezone"],
        preview_session_id="ctx-session",
        input={"q": "1"},
    )

    first = await api_client.post(
        f"/api/v1/agents/{agent['id']}/preview", headers=admin_headers, json=payload
    )
    assert first.status_code == 200, first.text
    second = await api_client.post(
        f"/api/v1/agents/{agent['id']}/preview",
        headers=admin_headers,
        json={**payload, "input": {"q": "2"}},
    )
    assert second.status_code == 200, second.text

    # 第二轮沿用首轮快照：上下文仍按声明注入，但上一轮的上下文没有进入历史
    assert second.json()["preview_snapshot"]["runtime_context"] == ["now", "timezone"]
    roles = [item.role for item in model_impl.requests[1].messages]
    assert roles == ["system", "system", "user", "assistant", "user"]
    assert sum("当前时间：" in item.content for item in model_impl.requests[1].messages) == 1
