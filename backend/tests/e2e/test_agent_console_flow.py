"""控制台验收：建配置 → 预览 → 存版本 → 发布 → 多轮会话 → 停用/启用恢复。"""

from __future__ import annotations

from tests.integration.agent_helpers import create_model, suffix
from tests.integration.test_agent_invoke import StaticModel, use_model


async def _create_question_prompt(api_client, headers: dict[str, str]) -> dict:
    created = await api_client.post(
        "/api/v1/prompts",
        headers=headers,
        json={
            "code": f"console-prompt-{suffix()}",
            "name": "Console Prompt",
            "messages": [
                {"role": "system", "content": "You are a console agent."},
                {"role": "user", "content": "Q: {{question}}"},
            ],
            "variables": [{"name": "question", "type": "string", "required": True}],
        },
    )
    assert created.status_code == 201, created.text
    versions = await api_client.get(
        f"/api/v1/prompts/{created.json()['id']}/versions", headers=headers
    )
    assert versions.status_code == 200, versions.text
    return versions.json()[0]


def _config(model_id: str, prompt_version_id: str) -> dict:
    return {
        "model_id": model_id,
        "prompt_version_id": prompt_version_id,
        "tool_version_ids": [],
        "params": {"temperature": 0.2},
        "policy": {"max_steps": 3, "timeout_s": 30},
    }


async def _create_agent_with_config(api_client, headers: dict[str, str]) -> dict:
    model = await create_model(api_client, headers)
    prompt = await _create_question_prompt(api_client, headers)
    created = await api_client.post(
        "/api/v1/agents",
        headers=headers,
        json={
            "code": f"console-{suffix()}",
            "name": "Console Agent",
            "config": _config(model["id"], prompt["id"]),
        },
    )
    assert created.status_code == 201, created.text
    return created.json()


async def _versions(api_client, headers: dict[str, str], agent_id: str) -> list[dict]:
    response = await api_client.get(f"/api/v1/agents/{agent_id}/versions", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


async def _invoke(api_client, headers: dict[str, str], agent_id: str, session_id: str):
    return await api_client.post(
        f"/api/v1/agents/{agent_id}/invoke",
        headers=headers,
        json={
            "env": "DEV",
            "input": {"question": "hi"},
            "session_id": session_id,
        },
    )


async def _session_item(api_client, headers: dict[str, str], agent_id: str, key: str) -> dict:
    response = await api_client.get(
        "/api/v1/sessions", headers=headers, params={"agent_id": agent_id}
    )
    assert response.status_code == 200, response.text
    items = [item for item in response.json()["items"] if item["session_key"] == key]
    assert len(items) == 1, response.text
    return items[0]


async def test_console_flow_create_preview_publish_multi_turn_and_lifecycle(
    api_client, admin_headers: dict[str, str], monkeypatch
) -> None:
    model = StaticModel(content="flow-ok")
    use_model(monkeypatch, model)

    agent = await _create_agent_with_config(api_client, admin_headers)
    versions = await _versions(api_client, admin_headers, agent["id"])
    assert len(versions) == 1
    first = versions[0]
    assert first["version_no"] == 1
    config = first["snapshot"]

    # 发布体检：初始配置的模型与 Prompt 均可用
    readiness = await api_client.get(
        f"/api/v1/agents/{agent['id']}/versions/{first['id']}/readiness",
        headers=admin_headers,
    )
    assert readiness.status_code == 200, readiness.text
    body = readiness.json()
    assert body["ready"] is True
    assert {item["role"] for item in body["bindings"]} == {"MODEL", "PROMPT"}
    assert body["preview"] is None

    # 预览：不改指针、不产生版本
    preview = await api_client.post(
        f"/api/v1/agents/{agent['id']}/preview",
        headers=admin_headers,
        json={
            **_config(config["model"]["model_id"], config["prompt"]["prompt_version_id"]),
            "env": "DEV",
            "input": {"question": "preview"},
            "preview_session_id": "flow-preview",
        },
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["status"] == "SUCCESS"
    assert preview.json()["output"] == "flow-ok"
    assert (await _versions(api_client, admin_headers, agent["id"]))[0]["id"] == first["id"]

    detail = await api_client.get(f"/api/v1/agents/{agent['id']}", headers=admin_headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["environments"]["DEV"] is None

    # v1 发布后建立会话，后续发布新版本不应切换该会话
    published = await api_client.post(
        f"/api/v1/agents/{agent['id']}/releases",
        headers=admin_headers,
        json={"agent_version_id": first["id"], "env": "DEV"},
    )
    assert published.status_code == 201, published.text

    calls_before_session = len(model.requests)
    old_turn = await _invoke(api_client, admin_headers, agent["id"], "flow-old")
    assert old_turn.status_code == 200, old_turn.text

    # 保存新版本（带基准）后仍不自动发布
    second = await api_client.post(
        f"/api/v1/agents/{agent['id']}/versions",
        headers=admin_headers,
        json={
            **_config(config["model"]["model_id"], config["prompt"]["prompt_version_id"]),
            "params": {"temperature": 0.9},
            "note": "tune temperature",
            "base_version_id": first["id"],
        },
    )
    assert second.status_code == 201, second.text
    assert second.json()["version_no"] == 2

    detail = await api_client.get(f"/api/v1/agents/{agent['id']}", headers=admin_headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["environments"]["DEV"]["version_no"] == 1

    republished = await api_client.post(
        f"/api/v1/agents/{agent['id']}/releases",
        headers=admin_headers,
        json={"agent_version_id": second.json()["id"], "env": "DEV"},
    )
    assert republished.status_code == 201, republished.text

    old_again = await _invoke(api_client, admin_headers, agent["id"], "flow-old")
    assert old_again.status_code == 200, old_again.text
    assert len(model.requests) == calls_before_session + 2
    assert "flow-ok" in [
        message.content for message in model.requests[calls_before_session + 1].messages
    ]

    newer_version = await api_client.post(
        f"/api/v1/agents/{agent['id']}/versions",
        headers=admin_headers,
        json={
            **_config(config["model"]["model_id"], config["prompt"]["prompt_version_id"]),
            "params": {"temperature": 1.1},
            "base_version_id": second.json()["id"],
        },
    )
    assert newer_version.status_code == 201, newer_version.text
    third_publish = await api_client.post(
        f"/api/v1/agents/{agent['id']}/releases",
        headers=admin_headers,
        json={"agent_version_id": newer_version.json()["id"], "env": "DEV"},
    )
    assert third_publish.status_code == 201, third_publish.text

    old_third = await _invoke(api_client, admin_headers, agent["id"], "flow-old")
    assert old_third.status_code == 200, old_third.text
    calls_before_fresh = len(model.requests)
    fresh = await _invoke(api_client, admin_headers, agent["id"], "flow-new")
    assert fresh.status_code == 200, fresh.text

    old_item = await _session_item(api_client, admin_headers, agent["id"], "flow-old")
    fresh_item = await _session_item(api_client, admin_headers, agent["id"], "flow-new")
    assert old_item["version_no"] == 1
    assert fresh_item["version_no"] == 3
    assert all(
        message.content != "flow-ok" for message in model.requests[calls_before_fresh].messages
    )

    # 停用后调用被拒；重新启用后恢复
    deactivated = await api_client.post(
        f"/api/v1/agents/{agent['id']}/deactivate", headers=admin_headers
    )
    assert deactivated.status_code == 200, deactivated.text
    rejected = await _invoke(api_client, admin_headers, agent["id"], "flow-old")
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["error"]["code"] == "AGENT_INACTIVE"

    activated = await api_client.post(
        f"/api/v1/agents/{agent['id']}/activate", headers=admin_headers
    )
    assert activated.status_code == 200, activated.text
    recovered = await _invoke(api_client, admin_headers, agent["id"], "flow-old")
    assert recovered.status_code == 200, recovered.text

    # 发布历史可回滚
    releases = await api_client.get(
        f"/api/v1/agents/{agent['id']}/releases", headers=admin_headers
    )
    assert releases.status_code == 200, releases.text
    history = releases.json()
    assert len(history) == 3
    rollback = await api_client.post(
        f"/api/v1/agents/{agent['id']}/rollback",
        headers=admin_headers,
        json={"env": "DEV", "release_id": history[-1]["release_id"]},
    )
    assert rollback.status_code == 200, rollback.text
