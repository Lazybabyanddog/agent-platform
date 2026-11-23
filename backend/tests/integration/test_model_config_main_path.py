"""主路径：建供应商 → 建模型（含 timeout）→ 探测（mock 上游）→ 版本 → 发布 → 调用。

生效超时的验收点：客户端超时 = min(模型参数 timeout, 本次运行预算)。
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from agent_platform.contexts.catalog.application.model_service import model_service
from agent_platform.contexts.execution.application.invocation_service import (
    _effective_client_timeout,
    invocation_service,
)
from agent_platform.contexts.execution.domain.ports import (
    ModelRequest,
    ModelResponse,
)
from tests.integration.agent_helpers import (
    create_agent,
    create_agent_version,
    create_prompt_version,
    suffix,
)

PARAM_TEMPLATE = {
    "temperature": {"type": "number", "min": 0, "max": 2, "default": 0.2},
    "timeout": {"type": "number", "min": 1, "max": 120, "default": 5},
}


class _EchoModel:
    async def chat(self, request: ModelRequest) -> ModelResponse:  # noqa: ARG002
        return ModelResponse(content="still-serving", input_tokens=1, output_tokens=2)


async def _create_provider_and_model(
    api_client: httpx.AsyncClient, headers: dict[str, str]
) -> tuple[dict[str, Any], dict[str, Any]]:
    provider = await api_client.post(
        "/api/v1/model-providers",
        headers=headers,
        json={"name": f"flow-provider-{suffix()}", "base_url": "https://stub.local/v1"},
    )
    assert provider.status_code == 201, provider.text

    model = await api_client.post(
        "/api/v1/models",
        headers=headers,
        json={
            "provider_id": provider.json()["id"],
            "code": f"flow-model-{suffix()}",
            "model_name": "gpt-4o-mini",
            "ctx_window": 128000,
            "param_template": PARAM_TEMPLATE,
        },
    )
    assert model.status_code == 201, model.text
    return provider.json(), model.json()


async def _publish_and_invoke(
    api_client: httpx.AsyncClient,
    headers: dict[str, str],
    agent_id: str,
    version_id: str,
) -> dict[str, Any]:
    release = await api_client.post(
        f"/api/v1/agents/{agent_id}/releases",
        headers=headers,
        json={"agent_version_id": version_id, "env": "DEV"},
    )
    assert release.status_code == 201, release.text

    response = await api_client.post(
        f"/api/v1/agents/{agent_id}/invoke",
        headers=headers,
        json={"env": "DEV", "input": {}},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_main_path_probe_publish_invoke_and_effective_timeout(
    api_client: httpx.AsyncClient,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _provider, model = await _create_provider_and_model(api_client, admin_headers)

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/models")
        return httpx.Response(200, json={"data": [{"id": "gpt-4o-mini"}]})

    monkeypatch.setattr(
        model_service, "_transport", httpx.MockTransport(handler), raising=True
    )

    probe = await api_client.post(
        f"/api/v1/models/{model['id']}/probe", headers=admin_headers
    )
    assert probe.status_code == 200, probe.text
    assert probe.json()["verdict"] == "OK"
    assert probe.json()["observed_model_name"] == "gpt-4o-mini"

    prompt = await create_prompt_version(api_client, admin_headers)
    agent = await create_agent(api_client, admin_headers)

    captured: list[tuple[dict[str, Any], float]] = []

    async def factory(actor: Any, snapshot: dict[str, Any], timeout_s: float) -> Any:
        captured.append((snapshot, timeout_s))
        return _EchoModel()

    monkeypatch.setattr(invocation_service, "_model_factory", factory)

    # 场景一：模板 timeout 比运行预算更紧 → 客户端超时取模板值。
    tight_template = await create_agent_version(
        api_client,
        admin_headers,
        agent["id"],
        model_id=model["id"],
        prompt_version_id=prompt["id"],
        params={"timeout": 10},
        policy={"timeout_s": 30, "max_steps": 3},
    )
    body = await _publish_and_invoke(
        api_client, admin_headers, agent["id"], tight_template["id"]
    )
    assert body["output"] == "still-serving"

    snapshot, budget = captured[-1]
    assert snapshot["model"]["params"]["timeout"] == 10
    assert budget == 30
    assert _effective_client_timeout(snapshot, budget) == 10

    # 场景二：运行预算比模板值更紧 → 客户端超时取预算。
    loose_template = await create_agent_version(
        api_client,
        admin_headers,
        agent["id"],
        model_id=model["id"],
        prompt_version_id=prompt["id"],
        params={"timeout": 60},
        policy={"timeout_s": 15, "max_steps": 3},
    )
    body = await _publish_and_invoke(
        api_client, admin_headers, agent["id"], loose_template["id"]
    )
    assert body["output"] == "still-serving"

    snapshot, budget = captured[-1]
    assert snapshot["model"]["params"]["timeout"] == 60
    assert budget == 15
    assert _effective_client_timeout(snapshot, budget) == 15
