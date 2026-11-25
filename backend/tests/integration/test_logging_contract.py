"""日志契约：请求标识贯穿、运行标识串联、正文不出现在日志里。"""

from __future__ import annotations

import json

import pytest

from agent_platform.contexts.execution.application.invocation_service import invocation_service
from agent_platform.contexts.execution.domain.enums import ModelErrorCode, RunErrorCode
from agent_platform.contexts.execution.domain.ports import ModelPortError
from agent_platform.settings import get_settings
from agent_platform.shared.logging import configure_logging
from tests.integration.agent_helpers import (
    create_agent,
    create_agent_version,
    create_model,
    create_prompt_version,
)
from tests.integration.conftest import TenantEnv

SECRET_ANSWER = "ANSWER-BODY-SECRET"


class _StaticModel:
    async def chat(self, _request):  # noqa: ANN001, ANN201
        from agent_platform.contexts.execution.domain.ports import ModelResponse

        return ModelResponse(content=SECRET_ANSWER, input_tokens=10, output_tokens=5)


class _FailingModel:
    async def chat(self, _request):  # noqa: ANN001, ANN201
        raise ModelPortError(ModelErrorCode.UPSTREAM, "upstream exploded")


def _use_model(monkeypatch: pytest.MonkeyPatch, model) -> None:  # noqa: ANN001
    async def factory(actor, snapshot, timeout_s):  # noqa: ANN001, ANN202
        return model

    monkeypatch.setattr(invocation_service, "_model_factory", factory)


async def _published(api_client, headers: dict[str, str]) -> dict:  # noqa: ANN001
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
    response = await api_client.post(
        f"/api/v1/agents/{agent['id']}/releases",
        headers=headers,
        json={"agent_version_id": version["id"], "env": "DEV"},
    )
    assert response.status_code == 201, response.text
    return agent


def _records(captured: str, event: str) -> list[dict]:
    return [
        item
        for item in (
            json.loads(line) for line in captured.splitlines() if line.startswith("{")
        )
        if item["event"] == event
    ]


@pytest.fixture(autouse=True)
def _ensure_pipeline():
    configure_logging(get_settings(), service="api")
    yield


async def test_invocation_logs_carry_trace_tenant_and_run(
    api_client,
    tenant_env: TenantEnv,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _use_model(monkeypatch, _StaticModel())
    agent = await _published(api_client, admin_headers)

    response = await api_client.post(
        f"/api/v1/agents/{agent['id']}/invoke",
        headers={**admin_headers, "X-Trace-Id": "trace-log-1"},
        json={"env": "DEV", "input": {}},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert response.headers["X-Trace-Id"] == "trace-log-1"

    captured = capsys.readouterr().out
    start = _records(captured, "invocation.start")[0]
    end = _records(captured, "invocation.end")[0]
    for record in (start, end):
        assert record["trace_id"] == "trace-log-1"
        assert record["tenant_id"] == str(tenant_env.tenant_id)
        assert record["run_id"] == body["run_id"]
        assert record["service"] == "api"
    assert start["agent_id"] == agent["id"]
    assert end["status"] == "SUCCESS"
    assert (end["steps"], end["tokens"]) == (1, 15)
    assert SECRET_ANSWER not in captured


async def test_failed_invocation_logs_error_code(
    api_client,
    tenant_env: TenantEnv,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _use_model(monkeypatch, _FailingModel())
    agent = await _published(api_client, admin_headers)

    response = await api_client.post(
        f"/api/v1/agents/{agent['id']}/invoke",
        headers={**admin_headers, "X-Trace-Id": "trace-log-2"},
        json={"env": "DEV", "input": {}},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    start = _records(capsys.readouterr().out, "invocation.end")[0]
    assert start["status"] == "FAILED"
    assert start["error_code"] == RunErrorCode.MODEL_ERROR.value
    assert start["run_id"] == body["run_id"]
