"""供应商/模型停用语义：阻断新版本与发布，不阻断已发布版本的运行。"""

from __future__ import annotations

import httpx
import pytest

from agent_platform.contexts.execution.application.invocation_service import invocation_service
from agent_platform.contexts.execution.domain.ports import ModelRequest, ModelResponse
from tests.integration.agent_helpers import (
    create_agent,
    create_agent_version,
    create_model,
    create_prompt_version,
)


class StaticModel:
    async def chat(self, request: ModelRequest) -> ModelResponse:  # noqa: ARG002
        return ModelResponse(content="still-serving", input_tokens=5, output_tokens=2)


def use_static_model(monkeypatch: pytest.MonkeyPatch) -> None:
    async def factory(actor, snapshot, timeout_s):  # noqa: ARG001
        return StaticModel()

    monkeypatch.setattr(invocation_service, "_model_factory", factory)


async def _disable_provider(
    api_client: httpx.AsyncClient, headers: dict[str, str], provider_id: str
) -> None:
    response = await api_client.patch(
        f"/api/v1/model-providers/{provider_id}",
        headers=headers,
        json={"status": "DISABLED"},
    )
    assert response.status_code == 200, response.text


async def test_disabled_provider_blocks_new_version(
    api_client: httpx.AsyncClient, admin_headers: dict[str, str]
) -> None:
    model = await create_model(api_client, admin_headers)
    prompt = await create_prompt_version(api_client, admin_headers)
    agent = await create_agent(api_client, admin_headers)
    await _disable_provider(api_client, admin_headers, model["provider_id"])

    response = await api_client.post(
        f"/api/v1/agents/{agent['id']}/versions",
        headers=admin_headers,
        json={
            "model_id": model["id"],
            "prompt_version_id": prompt["id"],
            "tool_version_ids": [],
            "params": {},
            "policy": {},
        },
    )
    assert response.status_code == 409, response.text
    problem = response.json()["error"]["details"][0]
    assert problem["field"] == "model_id"
    assert "disabled" in problem["reason"]
    # 原因能定位到具体供应商，便于界面直接展示
    assert "provider-" in problem["reason"]


async def test_disabled_provider_blocks_publish(
    api_client: httpx.AsyncClient, admin_headers: dict[str, str]
) -> None:
    model = await create_model(api_client, admin_headers)
    prompt = await create_prompt_version(api_client, admin_headers)
    agent = await create_agent(api_client, admin_headers)
    version = await create_agent_version(
        api_client, admin_headers, agent["id"], model_id=model["id"], prompt_version_id=prompt["id"]
    )

    await _disable_provider(api_client, admin_headers, model["provider_id"])

    response = await api_client.post(
        f"/api/v1/agents/{agent['id']}/releases",
        headers=admin_headers,
        json={"agent_version_id": version["id"], "env": "DEV"},
    )
    assert response.status_code == 409, response.text
    reasons = [item["reason"] for item in response.json()["error"]["details"]]
    assert any("disabled" in reason for reason in reasons)


async def test_published_version_keeps_running_after_provider_disabled(
    api_client: httpx.AsyncClient,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_static_model(monkeypatch)
    model = await create_model(api_client, admin_headers)
    prompt = await create_prompt_version(api_client, admin_headers)
    agent = await create_agent(api_client, admin_headers)
    version = await create_agent_version(
        api_client, admin_headers, agent["id"], model_id=model["id"], prompt_version_id=prompt["id"]
    )
    released = await api_client.post(
        f"/api/v1/agents/{agent['id']}/releases",
        headers=admin_headers,
        json={"agent_version_id": version["id"], "env": "DEV"},
    )
    assert released.status_code == 201, released.text

    await _disable_provider(api_client, admin_headers, model["provider_id"])

    response = await api_client.post(
        f"/api/v1/agents/{agent['id']}/invoke",
        headers=admin_headers,
        json={"env": "DEV", "input": {}},
    )
    assert response.status_code == 200, response.text
    assert response.json()["output"] == "still-serving"
