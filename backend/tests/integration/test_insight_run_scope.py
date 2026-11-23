"""指标口径与轨迹字段：默认排除预览、会话计数、种类与配置摘要。"""

from __future__ import annotations

import pytest

from agent_platform.contexts.execution.application.invocation_service import invocation_service
from agent_platform.contexts.execution.domain.ports import ModelRequest, ModelResponse
from tests.integration.agent_helpers import (
    create_agent,
    create_agent_version,
    create_model,
    create_prompt_version,
)
from tests.integration.conftest import TenantEnv


class OkModel:
    async def chat(self, request: ModelRequest) -> ModelResponse:
        del request
        return ModelResponse(content="ok", input_tokens=1, output_tokens=1)


def use_model(monkeypatch: pytest.MonkeyPatch) -> None:
    async def factory(actor, snapshot, timeout_s):
        del actor, snapshot, timeout_s
        return OkModel()

    monkeypatch.setattr(invocation_service, "_model_factory", factory)


async def _published_agent(api_client, headers: dict[str, str]) -> tuple[dict, dict, dict]:
    model = await create_model(api_client, headers)
    prompt = await create_prompt_version(api_client, headers)
    agent = await create_agent(api_client, headers)
    version = await create_agent_version(
        api_client,
        headers,
        agent["id"],
        model_id=model["id"],
        prompt_version_id=prompt["id"],
    )
    released = await api_client.post(
        f"/api/v1/agents/{agent['id']}/releases",
        headers=headers,
        json={"agent_version_id": version["id"], "env": "DEV"},
    )
    assert released.status_code == 201, released.text
    return agent, version, released.json()


async def test_metrics_exclude_preview_by_default(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str], monkeypatch
) -> None:
    use_model(monkeypatch)
    agent, _, _ = await _published_agent(api_client, admin_headers)

    invoke = await api_client.post(
        f"/api/v1/agents/{agent['id']}/invoke",
        headers=admin_headers,
        json={"env": "DEV", "input": {}},
    )
    assert invoke.status_code == 200, invoke.text

    preview = await api_client.post(
        f"/api/v1/agents/{agent['id']}/preview",
        headers=admin_headers,
        json={
            "model_id": (await create_model(api_client, admin_headers))["id"],
            "prompt_version_id": (await create_prompt_version(api_client, admin_headers))["id"],
            "tool_version_ids": [],
            "params": {},
            "policy": {"max_steps": 2},
            "input": {},
        },
    )
    assert preview.status_code == 200, preview.text

    default = await api_client.get(
        "/api/v1/metrics/runs",
        headers=admin_headers,
        params={"agent_id": agent["id"]},
    )
    assert default.json()["total"] == 1
    assert default.json()["sessions"] == 0

    included = await api_client.get(
        "/api/v1/metrics/runs",
        headers=admin_headers,
        params={"agent_id": agent["id"], "include_preview": "true"},
    )
    assert included.json()["total"] == 2

    preview_only = await api_client.get(
        "/api/v1/metrics/runs",
        headers=admin_headers,
        params={"agent_id": agent["id"], "kind": "PREVIEW"},
    )
    assert preview_only.json()["total"] == 1
    assert preview_only.json()["kind"] == "PREVIEW"

    runs = await api_client.get(
        "/api/v1/runs",
        headers=admin_headers,
        params={"agent_id": agent["id"], "kind": "PREVIEW"},
    )
    assert runs.status_code == 200, runs.text
    assert runs.json()["total"] == 1
    assert runs.json()["items"][0]["kind"] == "PREVIEW"


async def test_metrics_count_distinct_sessions(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str], monkeypatch
) -> None:
    use_model(monkeypatch)
    agent, _, _ = await _published_agent(api_client, admin_headers)
    for session_id in ("metric-s1", "metric-s1", None):
        payload = {"env": "DEV", "input": {}}
        if session_id is not None:
            payload["session_id"] = session_id
        response = await api_client.post(
            f"/api/v1/agents/{agent['id']}/invoke", headers=admin_headers, json=payload
        )
        assert response.status_code == 200, response.text

    response = await api_client.get(
        "/api/v1/metrics/runs",
        headers=admin_headers,
        params={"agent_id": agent["id"]},
    )
    body = response.json()
    assert body["total"] == 3
    assert body["sessions"] == 1


async def test_trace_exposes_kind_and_binding(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str], monkeypatch
) -> None:
    use_model(monkeypatch)
    agent, version, release = await _published_agent(api_client, admin_headers)

    invoke = await api_client.post(
        f"/api/v1/agents/{agent['id']}/invoke",
        headers=admin_headers,
        json={"env": "DEV", "input": {}},
    )
    preview = await api_client.post(
        f"/api/v1/agents/{agent['id']}/preview",
        headers=admin_headers,
        json={
            "model_id": (await create_model(api_client, admin_headers))["id"],
            "prompt_version_id": (await create_prompt_version(api_client, admin_headers))["id"],
            "tool_version_ids": [],
            "params": {},
            "policy": {"max_steps": 2},
            "input": {},
        },
    )

    invoke_trace = await api_client.get(
        f"/api/v1/runs/{invoke.json()['run_id']}/trace", headers=admin_headers
    )
    assert invoke_trace.status_code == 200, invoke_trace.text
    run = invoke_trace.json()["run"]
    assert run["kind"] == "INVOKE"
    assert run["agent_release_id"] == release["release_id"]
    assert run["agent_version_id"] == version["id"]
    assert run["config_hash"]

    preview_trace = await api_client.get(
        f"/api/v1/runs/{preview.json()['run_id']}/trace", headers=admin_headers
    )
    assert preview_trace.status_code == 200, preview_trace.text
    preview_run = preview_trace.json()["run"]
    assert preview_run["kind"] == "PREVIEW"
    assert preview_run["agent_release_id"] is None
    assert preview_run["agent_version_id"] is None
    assert preview_run["config_hash"] == preview.json()["config_hash"]
