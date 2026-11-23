"""运行指标：与 run 表一致、按 Agent 过滤与窗口校验。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from agent_platform.contexts.execution.application.invocation_service import invocation_service
from agent_platform.contexts.execution.domain.enums import ModelErrorCode, RunStatus
from agent_platform.contexts.execution.domain.ports import (
    ModelPortError,
    ModelRequest,
    ModelResponse,
)
from agent_platform.contexts.execution.infrastructure.tables import Run
from agent_platform.shared.db import session_scope
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


class FailingModel:
    async def chat(self, request: ModelRequest) -> ModelResponse:
        del request
        raise ModelPortError(ModelErrorCode.UPSTREAM, "boom")


def use_model(monkeypatch: pytest.MonkeyPatch, model) -> None:
    async def factory(actor, snapshot, timeout_s):
        return model

    monkeypatch.setattr(invocation_service, "_model_factory", factory)


async def _published_agent(api_client, headers: dict[str, str]) -> dict:
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
    return agent


async def _invoke(api_client, headers: dict[str, str], agent_id: str) -> dict:
    response = await api_client.post(
        f"/api/v1/agents/{agent_id}/invoke",
        headers=headers,
        json={"env": "DEV", "input": {}},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_metrics_match_run_table(
    api_client,
    tenant_env: TenantEnv,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = await _published_agent(api_client, admin_headers)

    use_model(monkeypatch, OkModel())
    await _invoke(api_client, admin_headers, agent["id"])
    await _invoke(api_client, admin_headers, agent["id"])

    use_model(monkeypatch, FailingModel())
    failed = await _invoke(api_client, admin_headers, agent["id"])
    assert failed["status"] == "FAILED"

    response = await api_client.get(
        "/api/v1/metrics/runs",
        headers=admin_headers,
        params={"agent_id": agent["id"]},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 3
    assert body["succeeded"] == 2
    assert body["failed"] == 1
    assert body["success_rate"] == pytest.approx(2 / 3, abs=1e-4)
    assert body["failure_rate"] == pytest.approx(1 / 3, abs=1e-4)
    assert body["p50_latency_ms"] is not None
    assert body["p95_latency_ms"] is not None
    assert body["p50_latency_ms"] <= body["p95_latency_ms"]

    async with session_scope(tenant_env.tenant_id) as session:
        total = await session.scalar(
            select(func.count())
            .select_from(Run)
            .where(Run.tenant_id == tenant_env.tenant_id, Run.agent_id == uuid.UUID(agent["id"]))
        )
        succeeded = await session.scalar(
            select(func.count())
            .select_from(Run)
            .where(
                Run.tenant_id == tenant_env.tenant_id,
                Run.agent_id == uuid.UUID(agent["id"]),
                Run.status == RunStatus.SUCCESS,
            )
        )
        failed_count = await session.scalar(
            select(func.count())
            .select_from(Run)
            .where(
                Run.tenant_id == tenant_env.tenant_id,
                Run.agent_id == uuid.UUID(agent["id"]),
                Run.status == RunStatus.FAILED,
            )
        )
    assert body["total"] == total
    assert body["succeeded"] == succeeded
    assert body["failed"] == failed_count


async def test_metrics_filter_by_agent_and_window(
    api_client, admin_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = await _published_agent(api_client, admin_headers)
    use_model(monkeypatch, OkModel())
    await _invoke(api_client, admin_headers, agent["id"])

    other = await api_client.get(
        "/api/v1/metrics/runs",
        headers=admin_headers,
        params={"agent_id": str(uuid.uuid4())},
    )
    assert other.status_code == 200, other.text
    assert other.json()["total"] == 0
    assert other.json()["success_rate"] == 0.0
    assert other.json()["p50_latency_ms"] is None

    future_start = (datetime.now(UTC) + timedelta(minutes=5)).isoformat()
    future = await api_client.get(
        "/api/v1/metrics/runs",
        headers=admin_headers,
        params={"agent_id": agent["id"], "start": future_start},
    )
    assert future.status_code == 200, future.text
    assert future.json()["total"] == 0


async def test_metrics_reject_inverted_window(
    api_client, admin_headers: dict[str, str]
) -> None:
    response = await api_client.get(
        "/api/v1/metrics/runs",
        headers=admin_headers,
        params={
            "start": "2026-02-01T00:00:00Z",
            "end": "2026-01-01T00:00:00Z",
        },
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"][0]["field"] == "start"
