"""执行轨迹：模型/工具调用落库、按时间排序与最耗时步骤。"""

from __future__ import annotations

import asyncio
import json
import uuid

import pytest
from sqlalchemy import func, select

from agent_platform.contexts.execution.application.invocation_service import invocation_service
from agent_platform.contexts.execution.domain.enums import ModelErrorCode
from agent_platform.contexts.execution.domain.ports import (
    ModelPortError,
    ModelRequest,
    ModelResponse,
    ToolCall,
    ToolResult,
)
from agent_platform.contexts.insight.infrastructure.repositories import (
    LLMCallRepository,
    ToolCallRepository,
)
from agent_platform.contexts.insight.infrastructure.tables import LLMCall
from agent_platform.shared.db import session_scope
from tests.integration.agent_helpers import (
    create_agent,
    create_agent_version,
    create_model,
    create_prompt_version,
    create_tool_version,
)
from tests.integration.conftest import TenantEnv


class ScriptedModel:
    """第一次请求发起工具调用，第二次给出最终答案。"""

    def __init__(self, tool_code: str) -> None:
        self.tool_code = tool_code
        self.calls = 0

    async def chat(self, request: ModelRequest) -> ModelResponse:
        del request
        self.calls += 1
        if self.calls == 1:
            return ModelResponse(
                content="",
                tool_calls=(
                    ToolCall(id="call-1", name=self.tool_code, arguments={"text": "hi"}),
                ),
                input_tokens=5,
                output_tokens=2,
            )
        return ModelResponse(content="done", input_tokens=3, output_tokens=4)


class FailingModel:
    async def chat(self, request: ModelRequest) -> ModelResponse:
        del request
        raise ModelPortError(ModelErrorCode.RATE_LIMITED, "slow down")


class SlowEchoTool:
    def __init__(self, *, delay_s: float = 0.08, ok: bool = True) -> None:
        self.delay_s = delay_s
        self.ok = ok
        self.calls = 0

    async def invoke(self, *, tool_code: str, tool_version_id: str, arguments):
        del tool_code, tool_version_id
        self.calls += 1
        await asyncio.sleep(self.delay_s)
        if not self.ok:
            return ToolResult(ok=False, content="bad input", error_code="INPUT_INVALID")
        return ToolResult(ok=True, content=json.dumps({"ok": True, "echo": arguments["text"]}))


def use_model(monkeypatch: pytest.MonkeyPatch, model) -> None:
    async def factory(actor, snapshot, timeout_s):
        return model

    monkeypatch.setattr(invocation_service, "_model_factory", factory)


def use_tool(monkeypatch: pytest.MonkeyPatch, tool) -> None:
    monkeypatch.setattr(invocation_service, "_tool_factory", lambda actor: tool)


async def _published_agent(
    api_client,
    headers: dict[str, str],
    *,
    tool_version: dict | None = None,
    policy: dict | None = None,
) -> tuple[dict, dict, dict]:
    model = await create_model(api_client, headers)
    prompt = await create_prompt_version(api_client, headers)
    agent = await create_agent(api_client, headers)
    version = await create_agent_version(
        api_client,
        headers,
        agent["id"],
        model_id=model["id"],
        prompt_version_id=prompt["id"],
        tool_version_ids=[tool_version["id"]] if tool_version else [],
        policy=policy,
    )
    released = await api_client.post(
        f"/api/v1/agents/{agent['id']}/releases",
        headers=headers,
        json={"agent_version_id": version["id"], "env": "DEV"},
    )
    assert released.status_code == 201, released.text
    return agent, version, released.json()


async def _create_tool(api_client, headers: dict[str, str]) -> dict:
    tool = await create_tool_version(api_client, headers)
    return tool


async def test_trace_records_llm_and_tool_calls_with_one_trace_id(
    api_client,
    tenant_env: TenantEnv,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool = await _create_tool(api_client, admin_headers)
    agent, _, _ = await _published_agent(
        api_client, admin_headers, tool_version=tool
    )
    model = ScriptedModel(tool["tool_code"])
    echo = SlowEchoTool()
    use_model(monkeypatch, model)
    use_tool(monkeypatch, echo)

    invoked = await api_client.post(
        f"/api/v1/agents/{agent['id']}/invoke",
        headers={**admin_headers, "X-Trace-Id": "trace-insight-1"},
        json={"env": "DEV", "input": {}},
    )
    assert invoked.status_code == 200, invoked.text
    assert invoked.json()["status"] == "SUCCESS"

    trace = await api_client.get(
        f"/api/v1/runs/{invoked.json()['run_id']}/trace", headers=admin_headers
    )
    assert trace.status_code == 200, trace.text
    body = trace.json()
    assert body["run"]["trace_id"] == "trace-insight-1"
    assert [entry["type"] for entry in body["entries"]] == [
        "LLM_CALL",
        "TOOL_CALL",
        "LLM_CALL",
    ]
    assert {entry["trace_id"] for entry in body["entries"]} == {"trace-insight-1"}
    assert body["entries"][0]["tokens"] == {"input": 5, "output": 2}
    assert body["entries"][1]["label"] == tool["tool_code"]
    assert [entry["is_slowest"] for entry in body["entries"]] == [False, True, False]
    assert body["slowest_span_id"] == body["entries"][1]["span_id"]
    assert body["slowest_latency_ms"] >= 50
    # 未开启正文留存，只返回摘要
    assert all(entry["content"] is None for entry in body["entries"])

    async with session_scope(tenant_env.tenant_id) as session:
        llm_calls = await LLMCallRepository().list_for_run(
            session, tenant_id=tenant_env.tenant_id, run_id=uuid.UUID(invoked.json()["run_id"])
        )
        tool_calls = await ToolCallRepository().list_for_run(
            session, tenant_id=tenant_env.tenant_id, run_id=uuid.UUID(invoked.json()["run_id"])
        )
    assert len(llm_calls) == 2
    assert len(tool_calls) == 1
    assert {call.trace_id for call in [*llm_calls, *tool_calls]} == {"trace-insight-1"}
    assert tool_calls[0].request_body is None
    assert tool_calls[0].response_body is None
    assert "hi" not in json.dumps(tool_calls[0].request_summary)


async def test_failed_llm_call_is_recorded_with_error_code(
    api_client,
    tenant_env: TenantEnv,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent, _, _ = await _published_agent(api_client, admin_headers)
    use_model(monkeypatch, FailingModel())

    invoked = await api_client.post(
        f"/api/v1/agents/{agent['id']}/invoke",
        headers=admin_headers,
        json={"env": "DEV", "input": {}},
    )
    assert invoked.status_code == 200, invoked.text
    assert invoked.json()["error"]["code"] == "MODEL_ERROR"

    trace = await api_client.get(
        f"/api/v1/runs/{invoked.json()['run_id']}/trace", headers=admin_headers
    )
    assert trace.status_code == 200, trace.text
    entries = trace.json()["entries"]
    assert len(entries) == 1
    assert entries[0]["status"] == "FAILED"
    assert entries[0]["error_code"] == "RATE_LIMITED"

    async with session_scope(tenant_env.tenant_id) as session:
        total = await session.scalar(
            select(func.count()).select_from(LLMCall).where(
                LLMCall.tenant_id == tenant_env.tenant_id
            )
        )
    assert total == 1


async def test_failed_tool_call_is_recorded(
    api_client,
    tenant_env: TenantEnv,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool = await _create_tool(api_client, admin_headers)
    agent, _, _ = await _published_agent(
        api_client, admin_headers, tool_version=tool
    )
    use_model(monkeypatch, ScriptedModel(tool["tool_code"]))
    use_tool(monkeypatch, SlowEchoTool(delay_s=0.01, ok=False))

    invoked = await api_client.post(
        f"/api/v1/agents/{agent['id']}/invoke",
        headers=admin_headers,
        json={"env": "DEV", "input": {}},
    )
    assert invoked.status_code == 200, invoked.text
    assert invoked.json()["error"]["code"] == "TOOL_ERROR"

    trace = await api_client.get(
        f"/api/v1/runs/{invoked.json()['run_id']}/trace", headers=admin_headers
    )
    entries = trace.json()["entries"]
    assert [entry["type"] for entry in entries] == ["LLM_CALL", "TOOL_CALL"]
    assert entries[1]["status"] == "FAILED"
    assert entries[1]["error_code"] == "INPUT_INVALID"

    async with session_scope(tenant_env.tenant_id) as session:
        stored = await ToolCallRepository().list_for_run(
            session, tenant_id=tenant_env.tenant_id, run_id=uuid.UUID(invoked.json()["run_id"])
        )
    assert len(stored) == 1
    assert stored[0].error_message == "bad input"


async def test_trace_of_unknown_run_returns_404(
    api_client, admin_headers: dict[str, str]
) -> None:
    response = await api_client.get(
        f"/api/v1/runs/{uuid.uuid4()}/trace", headers=admin_headers
    )
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "NOT_FOUND"


# ------------------------------------------------------------------ 按工具查询调用记录


async def _seed_tool_calls(
    api_client,
    tenant_env: TenantEnv,
    headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> dict:
    from datetime import UTC, datetime, timedelta

    from agent_platform.contexts.insight.domain.enums import CallStatus

    tool = await create_tool_version(api_client, headers)
    agent, _, _ = await _published_agent(api_client, headers)
    use_model(monkeypatch, FailingModel())
    invoked = await api_client.post(
        f"/api/v1/agents/{agent['id']}/invoke",
        headers=headers,
        json={"env": "DEV", "input": {}},
    )
    assert invoked.status_code == 200, invoked.text
    run_id = uuid.UUID(invoked.json()["run_id"])

    now = datetime.now(UTC)
    async with session_scope(tenant_env.tenant_id) as session:
        repo = ToolCallRepository()
        for index, (status, latency, error_code) in enumerate(
            [
                (CallStatus.SUCCESS, 100, None),
                (CallStatus.SUCCESS, 300, None),
                (CallStatus.FAILED, 500, "TOOL_TIMEOUT"),
            ]
        ):
            await repo.create(
                session,
                tenant_id=tenant_env.tenant_id,
                run_id=run_id,
                trace_id="trace-tool-calls",
                span_id=f"span-{index}",
                tool_id=uuid.UUID(tool["tool_id"]),
                tool_code=tool["tool_code"],
                tool_version_id=uuid.UUID(tool["id"]),
                request_digest="a" * 64,
                response_digest="b" * 64,
                request_summary={"keys": ["text"]},
                response_summary={"keys": ["ok"]},
                request_body={"text": "secret-input"},
                response_body={"ok": True},
                latency_ms=latency,
                status=status,
                error_code=error_code,
                started_at=now - timedelta(seconds=3 - index),
                ended_at=now - timedelta(seconds=3 - index) + timedelta(milliseconds=latency),
            )
    return tool


async def test_tool_call_overview_aggregates_mixed_results(
    api_client,
    tenant_env: TenantEnv,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool = await _seed_tool_calls(api_client, tenant_env, admin_headers, monkeypatch)

    response = await api_client.get(
        "/api/v1/tool-calls",
        headers=admin_headers,
        params={"tool_id": tool["tool_id"]},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 3, body
    assert body["succeeded"] == 2
    assert body["failed"] == 1
    assert body["success_rate"] == pytest.approx(2 / 3, abs=1e-4)
    assert body["avg_latency_ms"] == pytest.approx(300.0)
    assert body["errors"] == [{"code": "TOOL_TIMEOUT", "count": 1}]
    assert [item["status"] for item in body["items"]] == ["FAILED", "SUCCESS", "SUCCESS"]
    assert "request_body" not in response.text
    assert "response_body" not in response.text
    assert "secret-input" not in response.text

    limited = await api_client.get(
        "/api/v1/tool-calls",
        headers=admin_headers,
        params={"tool_id": tool["tool_id"], "limit": 2},
    )
    assert len(limited.json()["items"]) == 2

    invalid_window = await api_client.get(
        "/api/v1/tool-calls",
        headers=admin_headers,
        params={
            "tool_id": tool["tool_id"],
            "start": "2026-11-23T00:00:00Z",
            "end": "2026-11-22T00:00:00Z",
        },
    )
    assert invalid_window.status_code == 422, invalid_window.text
    assert invalid_window.json()["error"]["details"][0]["field"] == "start"


async def test_tool_call_overview_is_empty_for_unused_tool(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str]
) -> None:
    tool = await create_tool_version(api_client, admin_headers)

    response = await api_client.get(
        "/api/v1/tool-calls", headers=admin_headers, params={"tool_id": tool["tool_id"]}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 0
    assert body["success_rate"] is None
    assert body["avg_latency_ms"] is None
    assert body["items"] == []


async def test_tool_call_overview_is_tenant_scoped(
    api_client,
    tenant_env: TenantEnv,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_platform.bootstrap import bootstrap
    from tests.integration.conftest import purge_tenant

    tool = await _seed_tool_calls(api_client, tenant_env, admin_headers, monkeypatch)
    other_suffix = uuid.uuid4().hex[:8]
    other = await bootstrap(
        tenant_code=f"o{other_suffix}",
        tenant_name="Other Tenant",
        admin_email=f"other-{other_suffix}@example.com",
        admin_password="Passw0rd!456",
    )
    try:
        login = await api_client.post(
            "/api/v1/auth/login",
            json={
                "tenant_code": f"o{other_suffix}",
                "email": f"other-{other_suffix}@example.com",
                "password": "Passw0rd!456",
            },
        )
        other_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        response = await api_client.get(
            "/api/v1/tool-calls",
            headers=other_headers,
            params={"tool_id": tool["tool_id"]},
        )
        assert response.status_code == 200, response.text
        assert response.json()["total"] == 0
        assert response.json()["items"] == []
    finally:
        await purge_tenant(tenant_id=other.tenant_id, user_id=other.user_id)
