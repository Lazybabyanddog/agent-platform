"""执行期间并发发布：进行中的 run 仍按受理时固定的快照完成。"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from agent_platform.contexts.execution.application.invocation_service import invocation_service
from agent_platform.contexts.execution.domain.ports import ModelRequest, ModelResponse
from agent_platform.contexts.execution.infrastructure.repositories import RunRepository
from agent_platform.shared.db import session_scope
from tests.integration.agent_helpers import (
    add_prompt_version,
    create_agent,
    create_agent_version,
    create_model,
    create_prompt_version,
)
from tests.integration.conftest import TenantEnv


class BlockingModel:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.requests: list[ModelRequest] = []

    async def chat(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        self.started.set()
        await self.release.wait()
        return ModelResponse(content="v1-answer", input_tokens=1, output_tokens=1)


class RecordingModel:
    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def chat(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return ModelResponse(content="v2-answer", input_tokens=1, output_tokens=1)


def use_model(monkeypatch: pytest.MonkeyPatch, model) -> None:
    async def factory(actor, snapshot, timeout_s):
        return model

    monkeypatch.setattr(invocation_service, "_model_factory", factory)


async def _publish(api_client, headers: dict[str, str], agent_id: str, version_id: str) -> dict:
    response = await api_client.post(
        f"/api/v1/agents/{agent_id}/releases",
        headers=headers,
        json={"agent_version_id": version_id, "env": "DEV"},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_in_flight_run_keeps_its_snapshot_when_new_version_is_published(
    api_client,
    tenant_env: TenantEnv,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = await create_model(api_client, admin_headers)
    prompt_v1 = await create_prompt_version(api_client, admin_headers)
    prompt_v2 = await add_prompt_version(api_client, admin_headers, prompt_v1["prompt_id"])
    agent = await create_agent(api_client, admin_headers)
    version_v1 = await create_agent_version(
        api_client,
        admin_headers,
        agent["id"],
        model_id=model["id"],
        prompt_version_id=prompt_v1["id"],
    )
    version_v2 = await create_agent_version(
        api_client,
        admin_headers,
        agent["id"],
        model_id=model["id"],
        prompt_version_id=prompt_v2["id"],
        policy={"max_steps": 9},
    )
    release_v1 = await _publish(api_client, admin_headers, agent["id"], version_v1["id"])

    blocking = BlockingModel()
    use_model(monkeypatch, blocking)

    task = asyncio.create_task(
        api_client.post(
            f"/api/v1/agents/{agent['id']}/invoke",
            headers=admin_headers,
            json={"env": "DEV", "input": {}},
        )
    )
    await asyncio.wait_for(blocking.started.wait(), timeout=10)

    release_v2 = await _publish(api_client, admin_headers, agent["id"], version_v2["id"])
    pointer = await api_client.get(
        f"/api/v1/agents/{agent['id']}/pointers/DEV", headers=admin_headers
    )
    assert pointer.status_code == 200, pointer.text
    assert pointer.json()["release_id"] == release_v2["release_id"]

    blocking.release.set()
    response = await asyncio.wait_for(task, timeout=10)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "SUCCESS"
    assert body["output"] == "v1-answer"

    # 运行中的执行只用了受理时固定的 v1 快照
    assert blocking.requests[0].messages[0].content == "You are a support agent."
    async with session_scope(tenant_env.tenant_id) as session:
        run = await RunRepository().by_id(
            session,
            tenant_id=tenant_env.tenant_id,
            run_id=uuid.UUID(body["run_id"]),
        )
        assert run is not None
        assert run.agent_version_id == uuid.UUID(version_v1["id"])
        assert run.agent_release_id == uuid.UUID(release_v1["release_id"])

    # 新调用才使用新发布
    recording = RecordingModel()
    use_model(monkeypatch, recording)
    second = await api_client.post(
        f"/api/v1/agents/{agent['id']}/invoke",
        headers=admin_headers,
        json={"env": "DEV", "input": {}},
    )
    assert second.status_code == 200, second.text
    assert second.json()["output"] == "v2-answer"
    assert recording.requests[0].messages[0].content == "You are a support agent. Be brief."
    async with session_scope(tenant_env.tenant_id) as session:
        run = await RunRepository().by_id(
            session,
            tenant_id=tenant_env.tenant_id,
            run_id=uuid.UUID(second.json()["run_id"]),
        )
        assert run is not None
        assert run.agent_version_id == uuid.UUID(version_v2["id"])
        assert run.agent_release_id == uuid.UUID(release_v2["release_id"])
