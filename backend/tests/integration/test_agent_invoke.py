"""同步调用：受理链路、失败分类、运行记录与 checkpoint 引用。"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import func, select, text

from agent_platform.contexts.execution.application.invocation_service import invocation_service
from agent_platform.contexts.execution.domain.enums import (
    CallerType,
    ModelErrorCode,
    RunErrorCode,
    RunStatus,
)
from agent_platform.contexts.execution.domain.ports import (
    ModelPortError,
    ModelRequest,
    ModelResponse,
    ToolCall,
)
from agent_platform.contexts.execution.infrastructure.repositories import (
    CheckpointRepository,
    ExecutionEventRepository,
    RunRepository,
)
from agent_platform.contexts.execution.infrastructure.tables import Run
from agent_platform.shared.db import session_scope
from agent_platform.shared.errors import ResourceUnavailableError
from tests.integration.agent_helpers import (
    create_agent,
    create_agent_version,
    create_model,
    create_prompt_version,
    suffix,
)
from tests.integration.conftest import TenantEnv


class StaticModel:
    def __init__(self, *, content: str = "hello", input_tokens: int = 10, output_tokens: int = 5):
        self.content = content
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.requests: list[ModelRequest] = []

    async def chat(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return ModelResponse(
            content=self.content,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
        )


class FailingModel:
    async def chat(self, request: ModelRequest) -> ModelResponse:
        raise ModelPortError(ModelErrorCode.UPSTREAM, "upstream exploded")


class SlowModel:
    async def chat(self, request: ModelRequest) -> ModelResponse:
        await asyncio.sleep(1)
        return ModelResponse(content="too late")


class UnboundToolModel:
    async def chat(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(
            content="",
            tool_calls=(ToolCall(id="call-1", name="not-in-snapshot", arguments={}),),
        )


def use_model(monkeypatch: pytest.MonkeyPatch, model) -> None:
    async def factory(actor, snapshot, timeout_s):
        return model

    monkeypatch.setattr(invocation_service, "_model_factory", factory)


async def _publish(
    api_client, headers: dict[str, str], agent_id: str, version_id: str, env: str = "DEV"
) -> dict:
    response = await api_client.post(
        f"/api/v1/agents/{agent_id}/releases",
        headers=headers,
        json={"agent_version_id": version_id, "env": env},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _published_agent(
    api_client,
    headers: dict[str, str],
    *,
    policy: dict | None = None,
    prompt_version: dict | None = None,
) -> tuple[dict, dict, dict]:
    model = await create_model(api_client, headers)
    prompt = prompt_version or await create_prompt_version(api_client, headers)
    agent = await create_agent(api_client, headers)
    version = await create_agent_version(
        api_client,
        headers,
        agent["id"],
        model_id=model["id"],
        prompt_version_id=prompt["id"],
        policy=policy,
    )
    release = await _publish(api_client, headers, agent["id"], version["id"])
    return agent, version, release


async def _create_prompt_with_required_variable(api_client, headers: dict[str, str]) -> dict:
    created = await api_client.post(
        "/api/v1/prompts",
        headers=headers,
        json={
            "code": f"prompt-{suffix()}",
            "name": "QA",
            "messages": [{"role": "user", "content": "Q: {{question}}"}],
            "variables": [{"name": "question", "type": "string", "required": True}],
        },
    )
    assert created.status_code == 201, created.text
    versions = await api_client.get(
        f"/api/v1/prompts/{created.json()['id']}/versions", headers=headers
    )
    assert versions.status_code == 200, versions.text
    return versions.json()[0]


async def _count_runs(tenant_id: uuid.UUID) -> int:
    async with session_scope(tenant_id) as session:
        return int(
            await session.scalar(
                select(func.count()).select_from(Run).where(Run.tenant_id == tenant_id)
            )
            or 0
        )


async def test_invoke_success_records_run_events_and_checkpoint(
    api_client,
    tenant_env: TenantEnv,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = StaticModel(content="hello")
    use_model(monkeypatch, model)
    agent, version, release = await _published_agent(api_client, admin_headers)

    response = await api_client.post(
        f"/api/v1/agents/{agent['id']}/invoke",
        headers={**admin_headers, "X-Trace-Id": "trace-invoke-success"},
        json={"env": "DEV", "input": {}},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "SUCCESS"
    assert body["output"] == "hello"
    assert body["usage"] == {"steps": 1, "tokens": 15}
    assert body["trace_id"] == "trace-invoke-success"
    assert response.headers["X-Trace-Id"] == body["trace_id"]

    run_id = uuid.UUID(body["run_id"])
    async with session_scope(tenant_env.tenant_id) as session:
        run = await RunRepository().by_id(
            session, tenant_id=tenant_env.tenant_id, run_id=run_id
        )
        assert run is not None
        # 5.1：运行记录固定发布记录、版本与追踪标识
        assert run.agent_release_id == uuid.UUID(release["release_id"])
        assert run.agent_version_id == uuid.UUID(version["id"])
        assert run.trace_id == body["trace_id"]
        assert run.status == RunStatus.SUCCESS
        assert run.caller_type == CallerType.USER
        assert run.finished_at is not None
        assert run.error is None

        events = await ExecutionEventRepository().list_for_run(
            session, tenant_id=tenant_env.tenant_id, run_id=run_id
        )
        assert [event.type for event in events] == ["run.completed"]
        assert events[0].payload["output"] == "hello"

        # 5.7：只保存可定位的 checkpoint 引用
        ref = await CheckpointRepository().by_run(
            session, tenant_id=tenant_env.tenant_id, run_id=run_id
        )
        assert ref is not None
        assert ref.thread_id == str(run_id)
        assert ref.checkpoint_id

    # checkpoint_ref 只保留定位信息，不复制 LangGraph 内部结构
    async with session_scope(tenant_env.tenant_id) as session:
        rows = await session.execute(
            text(
                "select column_name from information_schema.columns "
                "where table_schema = 'execution' and table_name = 'checkpoint_ref'"
            )
        )
    assert {row[0] for row in rows} == {
        "id",
        "tenant_id",
        "run_id",
        "thread_id",
        "checkpoint_id",
        "created_at",
    }


async def test_invoke_without_release_returns_404(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str], monkeypatch
) -> None:
    use_model(monkeypatch, StaticModel())
    model = await create_model(api_client, admin_headers)
    prompt = await create_prompt_version(api_client, admin_headers)
    agent = await create_agent(api_client, admin_headers)
    await create_agent_version(
        api_client,
        admin_headers,
        agent["id"],
        model_id=model["id"],
        prompt_version_id=prompt["id"],
    )

    response = await api_client.post(
        f"/api/v1/agents/{agent['id']}/invoke",
        headers=admin_headers,
        json={"env": "DEV", "input": {}},
    )
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "NOT_FOUND"
    assert await _count_runs(tenant_env.tenant_id) == 0


async def test_invoke_without_permission_returns_403_and_creates_no_run(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str], monkeypatch
) -> None:
    use_model(monkeypatch, StaticModel())
    agent, _, _ = await _published_agent(api_client, admin_headers)
    created = await api_client.post(
        "/api/v1/api-keys",
        headers=admin_headers,
        json={"name": "reader", "scopes": ["run:read"]},
    )
    assert created.status_code == 201, created.text
    reader = {"Authorization": f"ApiKey {created.json()['secret']}"}

    response = await api_client.post(
        f"/api/v1/agents/{agent['id']}/invoke",
        headers=reader,
        json={"env": "DEV", "input": {}},
    )
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "FORBIDDEN"
    assert await _count_runs(tenant_env.tenant_id) == 0


async def test_invoke_validation_error_creates_no_run(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str], monkeypatch
) -> None:
    use_model(monkeypatch, StaticModel())
    prompt = await _create_prompt_with_required_variable(api_client, admin_headers)
    agent, _, _ = await _published_agent(
        api_client, admin_headers, prompt_version=prompt
    )

    missing = await api_client.post(
        f"/api/v1/agents/{agent['id']}/invoke",
        headers=admin_headers,
        json={"env": "DEV", "input": {}},
    )
    assert missing.status_code == 422, missing.text
    assert missing.json()["error"]["details"][0]["field"] == "variables.question"

    wrong_type = await api_client.post(
        f"/api/v1/agents/{agent['id']}/invoke",
        headers=admin_headers,
        json={"env": "DEV", "input": {"question": 42}},
    )
    assert wrong_type.status_code == 422, wrong_type.text
    assert wrong_type.json()["error"]["details"][0]["field"] == "variables.question"
    assert await _count_runs(tenant_env.tenant_id) == 0


async def test_invoke_over_sync_limit_asks_for_async(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str], monkeypatch
) -> None:
    use_model(monkeypatch, StaticModel())
    agent, _, _ = await _published_agent(api_client, admin_headers)

    response = await api_client.post(
        f"/api/v1/agents/{agent['id']}/invoke",
        headers=admin_headers,
        json={"env": "DEV", "input": {}, "timeout_s": 999},
    )
    assert response.status_code == 400, response.text
    error = response.json()["error"]
    assert error["code"] == "SYNC_TIMEOUT_EXCEEDED"
    assert error["details"][0]["code"] == "ASYNC_REQUIRED"
    assert await _count_runs(tenant_env.tenant_id) == 0


async def test_model_error_is_classified_in_run_record(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str], monkeypatch
) -> None:
    use_model(monkeypatch, FailingModel())
    agent, _, release = await _published_agent(api_client, admin_headers)

    response = await api_client.post(
        f"/api/v1/agents/{agent['id']}/invoke",
        headers=admin_headers,
        json={"env": "DEV", "input": {}},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "FAILED"
    assert body["error"]["code"] == RunErrorCode.MODEL_ERROR.value
    assert body["error"]["details"]["model_error_code"] == ModelErrorCode.UPSTREAM.value

    async with session_scope(tenant_env.tenant_id) as session:
        run = await RunRepository().by_id(
            session, tenant_id=tenant_env.tenant_id, run_id=uuid.UUID(body["run_id"])
        )
        assert run is not None
        assert run.status == RunStatus.FAILED
        assert run.agent_release_id == uuid.UUID(release["release_id"])
        assert run.error["code"] == RunErrorCode.MODEL_ERROR.value
        events = await ExecutionEventRepository().list_for_run(
            session, tenant_id=tenant_env.tenant_id, run_id=run.id
        )
        assert [event.type for event in events] == ["run.failed"]


async def test_budget_exceeded_is_classified_in_run_record(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str], monkeypatch
) -> None:
    use_model(monkeypatch, StaticModel(input_tokens=10, output_tokens=10))
    agent, _, _ = await _published_agent(
        api_client, admin_headers, policy={"token_budget": 5}
    )

    response = await api_client.post(
        f"/api/v1/agents/{agent['id']}/invoke",
        headers=admin_headers,
        json={"env": "DEV", "input": {}},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "FAILED"
    assert body["error"]["code"] == RunErrorCode.BUDGET_EXCEEDED.value

    async with session_scope(tenant_env.tenant_id) as session:
        run = await RunRepository().by_id(
            session, tenant_id=tenant_env.tenant_id, run_id=uuid.UUID(body["run_id"])
        )
        assert run is not None
        assert run.error["code"] == RunErrorCode.BUDGET_EXCEEDED.value
        assert run.error["details"]["limit"] == "TOKEN_BUDGET"


async def test_api_key_caller_is_recorded_as_api_key(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str], monkeypatch
) -> None:
    use_model(monkeypatch, StaticModel(content="from-key"))
    agent, _, _ = await _published_agent(api_client, admin_headers)
    created = await api_client.post(
        "/api/v1/api-keys",
        headers=admin_headers,
        json={"name": "invoker", "scopes": ["agent:invoke"]},
    )
    assert created.status_code == 201, created.text
    key_headers = {"Authorization": f"ApiKey {created.json()['secret']}"}

    response = await api_client.post(
        f"/api/v1/agents/{agent['id']}/invoke",
        headers=key_headers,
        json={"env": "DEV", "input": {}},
    )
    assert response.status_code == 200, response.text
    assert response.json()["output"] == "from-key"

    async with session_scope(tenant_env.tenant_id) as session:
        run = await RunRepository().by_id(
            session,
            tenant_id=tenant_env.tenant_id,
            run_id=uuid.UUID(response.json()["run_id"]),
        )
        assert run is not None
        assert run.caller_type == CallerType.API_KEY


async def test_timeout_is_classified_in_run_record(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str], monkeypatch
) -> None:
    use_model(monkeypatch, SlowModel())
    agent, _, _ = await _published_agent(
        api_client, admin_headers, policy={"timeout_s": 0.05}
    )

    response = await api_client.post(
        f"/api/v1/agents/{agent['id']}/invoke",
        headers=admin_headers,
        json={"env": "DEV", "input": {}},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "FAILED"
    assert body["error"]["code"] == RunErrorCode.TIMEOUT.value

    async with session_scope(tenant_env.tenant_id) as session:
        run = await RunRepository().by_id(
            session, tenant_id=tenant_env.tenant_id, run_id=uuid.UUID(body["run_id"])
        )
        assert run is not None
        assert run.error["code"] == RunErrorCode.TIMEOUT.value
        assert run.error["details"]["timeout_s"] == 0.05


async def test_model_unavailable_is_recorded_as_failed_run(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str], monkeypatch
) -> None:
    async def broken_factory(actor, snapshot, timeout_s):
        raise ResourceUnavailableError("model is no longer available")

    monkeypatch.setattr(invocation_service, "_model_factory", broken_factory)
    agent, _, _ = await _published_agent(api_client, admin_headers)

    response = await api_client.post(
        f"/api/v1/agents/{agent['id']}/invoke",
        headers=admin_headers,
        json={"env": "DEV", "input": {}},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "FAILED"
    assert body["error"]["code"] == RunErrorCode.MODEL_ERROR.value
    assert body["error"]["details"]["reason"] == "model_unavailable"

    async with session_scope(tenant_env.tenant_id) as session:
        run = await RunRepository().by_id(
            session, tenant_id=tenant_env.tenant_id, run_id=uuid.UUID(body["run_id"])
        )
        assert run is not None
        assert run.status == RunStatus.FAILED
        assert run.finished_at is not None


async def test_unbound_tool_call_is_classified_as_tool_error(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str], monkeypatch
) -> None:
    use_model(monkeypatch, UnboundToolModel())
    agent, _, _ = await _published_agent(api_client, admin_headers)

    response = await api_client.post(
        f"/api/v1/agents/{agent['id']}/invoke",
        headers=admin_headers,
        json={"env": "DEV", "input": {}},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "FAILED"
    assert body["error"]["code"] == RunErrorCode.TOOL_ERROR.value

    async with session_scope(tenant_env.tenant_id) as session:
        run = await RunRepository().by_id(
            session, tenant_id=tenant_env.tenant_id, run_id=uuid.UUID(body["run_id"])
        )
        assert run is not None
        assert run.error["code"] == RunErrorCode.TOOL_ERROR.value
        assert run.error["details"]["tool_code"] == "not-in-snapshot"
