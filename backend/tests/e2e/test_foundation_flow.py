"""端到端验收：完整主链、回滚、安全阻断与审计追溯。

用例全部走 HTTP API（ASGI 应用 + 真实 PostgreSQL），模型/工具上游用确定性替身，
与集成测试保持同一套装配方式。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select

from agent_platform.bootstrap import bootstrap
from agent_platform.contexts.execution.application.invocation_service import invocation_service
from agent_platform.contexts.execution.domain.ports import (
    ModelRequest,
    ModelResponse,
    ToolCall,
    ToolResult,
)
from agent_platform.contexts.execution.infrastructure.tables import Run
from agent_platform.shared.db import session_scope
from tests.integration.agent_helpers import (
    create_agent,
    create_agent_version,
    create_model,
    create_tool_version,
)
from tests.integration.conftest import ADMIN_PASSWORD, TenantEnv, purge_tenant

PROD = "PROD"


# --------------------------------------------------------------------- 替身


class RecordingModel:
    """记录每次请求；可选地先请求一次工具再给出最终答案。"""

    def __init__(self, *, tool_code: str | None = None, content: str = "hello") -> None:
        self.tool_code = tool_code
        self.content = content
        self.requests: list[ModelRequest] = []

    async def chat(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if self.tool_code and not any(message.role == "tool" for message in request.messages):
            return ModelResponse(
                content="",
                tool_calls=(
                    ToolCall(id="call-1", name=self.tool_code, arguments={"text": "hi"}),
                ),
            )
        return ModelResponse(content=self.content, input_tokens=10, output_tokens=5)


class EchoTool:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def invoke(
        self, *, tool_code: str, tool_version_id: str, arguments: dict[str, Any]
    ) -> ToolResult:
        self.calls.append((tool_code, arguments))
        return ToolResult(ok=True, content=json.dumps({"ok": True, "echo": arguments.get("text")}))


def use_model(monkeypatch: pytest.MonkeyPatch, model: Any) -> None:
    async def factory(actor, snapshot, timeout_s):
        return model

    monkeypatch.setattr(invocation_service, "_model_factory", factory)


def use_tool(monkeypatch: pytest.MonkeyPatch, tool: Any) -> None:
    monkeypatch.setattr(invocation_service, "_tool_factory", lambda actor: tool)


# --------------------------------------------------------------------- 构造


async def _create_prompt(
    api_client: httpx.AsyncClient, headers: dict[str, str], *, text: str
) -> dict[str, Any]:
    created = await api_client.post(
        "/api/v1/prompts",
        headers=headers,
        json={
            "code": f"e2e-prompt-{uuid.uuid4().hex[:6]}",
            "name": "E2E Support",
            "messages": [{"role": "system", "content": text}],
            "variables": [],
        },
    )
    assert created.status_code == 201, created.text
    versions = await api_client.get(
        f"/api/v1/prompts/{created.json()['id']}/versions", headers=headers
    )
    assert versions.status_code == 200, versions.text
    return {**versions.json()[0], "prompt_id": created.json()["id"]}


async def _add_prompt_version(
    api_client: httpx.AsyncClient,
    headers: dict[str, str],
    prompt_id: str,
    *,
    text: str,
) -> dict[str, Any]:
    response = await api_client.post(
        f"/api/v1/prompts/{prompt_id}/versions",
        headers=headers,
        json={"messages": [{"role": "system", "content": text}], "variables": []},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _publish(
    api_client: httpx.AsyncClient,
    headers: dict[str, str],
    agent_id: str,
    version_id: str,
    *,
    env: str = PROD,
) -> dict[str, Any]:
    response = await api_client.post(
        f"/api/v1/agents/{agent_id}/releases",
        headers=headers,
        json={"agent_version_id": version_id, "env": env},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _invoke(
    api_client: httpx.AsyncClient,
    headers: dict[str, str],
    agent_id: str,
    *,
    trace_id: str | None = None,
) -> dict[str, Any]:
    extra = {"X-Trace-Id": trace_id} if trace_id else {}
    response = await api_client.post(
        f"/api/v1/agents/{agent_id}/invoke",
        headers={**headers, **extra},
        json={"env": PROD, "input": {}},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _run_row(tenant_id: uuid.UUID, run_id: str) -> Run:
    async with session_scope(tenant_id) as session:
        run = await session.get(Run, uuid.UUID(run_id))
    assert run is not None and run.tenant_id == tenant_id
    return run


async def _count_runs(tenant_id: uuid.UUID) -> int:
    async with session_scope(tenant_id) as session:
        return int(
            await session.scalar(
                select(func.count()).select_from(Run).where(Run.tenant_id == tenant_id)
            )
            or 0
        )


@dataclass(frozen=True, slots=True)
class OtherTenant:
    env: TenantEnv
    headers: dict[str, str]


async def _create_other_tenant(api_client: httpx.AsyncClient) -> OtherTenant:
    suffix = uuid.uuid4().hex[:10]
    tenant_code = f"o{suffix}"
    email = f"other-{suffix}@example.com"
    result = await bootstrap(
        tenant_code=tenant_code,
        tenant_name=f"Other Tenant {suffix}",
        admin_email=email,
        admin_password=ADMIN_PASSWORD,
    )
    login = await api_client.post(
        "/api/v1/auth/login",
        json={
            "tenant_code": tenant_code,
            "email": email,
            "password": ADMIN_PASSWORD,
        },
    )
    assert login.status_code == 200, login.text
    env = TenantEnv(
        tenant_id=result.tenant_id,
        tenant_code=tenant_code,
        email=email,
        user_id=result.user_id,
        password=ADMIN_PASSWORD,
    )
    return OtherTenant(
        env=env, headers={"Authorization": f"Bearer {login.json()['access_token']}"}
    )


# --------------------------------------------------------------------- 8.1


async def test_full_flow_publish_invoke_and_trace(
    api_client: httpx.AsyncClient,
    tenant_env: TenantEnv,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool = EchoTool()
    use_tool(monkeypatch, tool)

    registered_model = await create_model(api_client, admin_headers)
    prompt = await _create_prompt(api_client, admin_headers, text="You are the e2e assistant.")
    tool_version = await create_tool_version(api_client, admin_headers)
    tool_code = tool_version["tool_code"]
    model = RecordingModel(tool_code=tool_code)
    use_model(monkeypatch, model)
    agent = await create_agent(api_client, admin_headers)
    version = await create_agent_version(
        api_client,
        admin_headers,
        agent["id"],
        model_id=registered_model["id"],
        prompt_version_id=prompt["id"],
        tool_version_ids=[tool_version["id"]],
    )
    release = await _publish(api_client, admin_headers, agent["id"], version["id"])

    body = await _invoke(api_client, admin_headers, agent["id"], trace_id="e2e-full-flow")

    assert body["status"] == "SUCCESS", body
    assert body["output"] == "hello"
    assert body["trace_id"] == "e2e-full-flow"
    assert tool.calls == [(tool_code, {"text": "hi"})]

    run = await _run_row(tenant_env.tenant_id, body["run_id"])
    assert run.agent_release_id == uuid.UUID(release["release_id"])
    assert run.agent_version_id == uuid.UUID(version["id"])
    assert run.trace_id == "e2e-full-flow"

    trace = await api_client.get(f"/api/v1/runs/{body['run_id']}/trace", headers=admin_headers)
    assert trace.status_code == 200, trace.text
    payload = trace.json()
    assert payload["run"]["trace_id"] == "e2e-full-flow"
    assert payload["run"]["status"] == "SUCCESS"
    types = [entry["type"] for entry in payload["entries"]]
    assert types.count("LLM_CALL") == 2
    assert types.count("TOOL_CALL") == 1
    assert {entry["trace_id"] for entry in payload["entries"]} == {"e2e-full-flow"}
    assert payload["slowest_span_id"] in {entry["span_id"] for entry in payload["entries"]}
    tool_entries = [entry for entry in payload["entries"] if entry["type"] == "TOOL_CALL"]
    assert tool_entries[0]["status"] == "SUCCESS"
    assert tool_entries[0]["label"] == tool_code


# --------------------------------------------------------------------- 8.2


async def test_rollback_switches_new_calls_and_keeps_run_history(
    api_client: httpx.AsyncClient,
    tenant_env: TenantEnv,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = RecordingModel()
    use_model(monkeypatch, model)

    registered_model = await create_model(api_client, admin_headers)
    prompt = await _create_prompt(api_client, admin_headers, text="You are the v1 assistant.")
    agent = await create_agent(api_client, admin_headers)
    version_v1 = await create_agent_version(
        api_client,
        admin_headers,
        agent["id"],
        model_id=registered_model["id"],
        prompt_version_id=prompt["id"],
    )
    release_v1 = await _publish(api_client, admin_headers, agent["id"], version_v1["id"])
    run_v1 = await _invoke(api_client, admin_headers, agent["id"])

    prompt_v2 = await _add_prompt_version(
        api_client,
        admin_headers,
        prompt["prompt_id"],
        text="You are the v2 assistant.",
    )
    version_v2 = await create_agent_version(
        api_client,
        admin_headers,
        agent["id"],
        model_id=registered_model["id"],
        prompt_version_id=prompt_v2["id"],
    )
    release_v2 = await _publish(api_client, admin_headers, agent["id"], version_v2["id"])
    run_v2 = await _invoke(api_client, admin_headers, agent["id"])

    rolled = await api_client.post(
        f"/api/v1/agents/{agent['id']}/rollback",
        headers=admin_headers,
        json={"env": PROD, "release_id": release_v1["release_id"]},
    )
    assert rolled.status_code == 200, rolled.text
    assert rolled.json()["release_id"] == release_v1["release_id"]

    run_v3 = await _invoke(api_client, admin_headers, agent["id"])

    def system_text(request: ModelRequest) -> str:
        return " ".join(message.content for message in request.messages)

    assert "v1 assistant" in system_text(model.requests[0])
    assert "v2 assistant" in system_text(model.requests[1])
    # 回滚后的新调用必须回到 v1
    assert "v1 assistant" in system_text(model.requests[2])
    assert "v2 assistant" not in system_text(model.requests[2])

    row1 = await _run_row(tenant_env.tenant_id, run_v1["run_id"])
    row2 = await _run_row(tenant_env.tenant_id, run_v2["run_id"])
    row3 = await _run_row(tenant_env.tenant_id, run_v3["run_id"])
    # 历史运行固定原发布记录，回滚不改写
    assert row1.agent_release_id == uuid.UUID(release_v1["release_id"])
    assert row1.agent_version_id == uuid.UUID(version_v1["id"])
    assert row2.agent_release_id == uuid.UUID(release_v2["release_id"])
    assert row2.agent_version_id == uuid.UUID(version_v2["id"])
    assert row3.agent_release_id == uuid.UUID(release_v1["release_id"])
    assert row3.agent_version_id == uuid.UUID(version_v1["id"])

    releases = await api_client.get(
        f"/api/v1/agents/{agent['id']}/releases", headers=admin_headers, params={"env": PROD}
    )
    assert releases.status_code == 200, releases.text
    assert len(releases.json()) == 2
    pointer = await api_client.get(
        f"/api/v1/agents/{agent['id']}/pointers/{PROD}", headers=admin_headers
    )
    assert pointer.status_code == 200, pointer.text
    assert pointer.json()["release_id"] == release_v1["release_id"]


# --------------------------------------------------------------------- 8.3


async def test_cross_tenant_access_and_invalid_api_key_are_blocked(
    api_client: httpx.AsyncClient,
    tenant_env: TenantEnv,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_model(monkeypatch, RecordingModel(content="owner-only"))
    registered_model = await create_model(api_client, admin_headers)
    prompt = await _create_prompt(api_client, admin_headers, text="You are the owner agent.")
    agent = await create_agent(api_client, admin_headers)
    version = await create_agent_version(
        api_client,
        admin_headers,
        agent["id"],
        model_id=registered_model["id"],
        prompt_version_id=prompt["id"],
    )
    await _publish(api_client, admin_headers, agent["id"], version["id"])
    first = await _invoke(api_client, admin_headers, agent["id"])
    assert await _count_runs(tenant_env.tenant_id) == 1

    other = await _create_other_tenant(api_client)
    try:
        for path in (
            f"/api/v1/agents/{agent['id']}",
            f"/api/v1/agents/{agent['id']}/versions",
            f"/api/v1/agents/{agent['id']}/releases",
            f"/api/v1/runs/{first['run_id']}/trace",
        ):
            response = await api_client.get(path, headers=other.headers)
            assert response.status_code == 404, (path, response.text)
            assert response.json()["error"]["code"] == "NOT_FOUND"

        cross_invoke = await api_client.post(
            f"/api/v1/agents/{agent['id']}/invoke",
            headers=other.headers,
            json={"env": PROD, "input": {}},
        )
        assert cross_invoke.status_code == 404, cross_invoke.text
        assert await _count_runs(tenant_env.tenant_id) == 1
    finally:
        await purge_tenant(tenant_id=other.env.tenant_id, user_id=other.env.user_id)

    created = await api_client.post(
        "/api/v1/api-keys",
        headers=admin_headers,
        json={"name": "e2e-invoker", "scopes": ["agent:invoke"]},
    )
    assert created.status_code == 201, created.text
    key_headers = {"Authorization": f"ApiKey {created.json()['secret']}"}
    via_key = await api_client.post(
        f"/api/v1/agents/{agent['id']}/invoke",
        headers=key_headers,
        json={"env": PROD, "input": {}},
    )
    assert via_key.status_code == 200, via_key.text
    assert await _count_runs(tenant_env.tenant_id) == 2

    disabled = await api_client.delete(
        f"/api/v1/api-keys/{created.json()['id']}", headers=admin_headers
    )
    assert disabled.status_code == 204, disabled.text
    for headers in (
        key_headers,
        {"Authorization": "ApiKey ak_invalid.secret"},
    ):
        blocked = await api_client.post(
            f"/api/v1/agents/{agent['id']}/invoke",
            headers=headers,
            json={"env": PROD, "input": {}},
        )
        assert blocked.status_code == 401, blocked.text
        assert blocked.json()["error"]["code"] == "UNAUTHORIZED"
    assert await _count_runs(tenant_env.tenant_id) == 2


async def test_revoked_tool_grant_blocks_invocation(
    api_client: httpx.AsyncClient,
    tenant_env: TenantEnv,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool_code = f"echo-{uuid.uuid4().hex[:6]}"
    use_model(monkeypatch, RecordingModel(tool_code=tool_code))

    registered_model = await create_model(api_client, admin_headers)
    prompt = await _create_prompt(api_client, admin_headers, text="You are the tool agent.")
    tool_version = await create_tool_version(api_client, admin_headers)
    agent = await create_agent(api_client, admin_headers)
    version = await create_agent_version(
        api_client,
        admin_headers,
        agent["id"],
        model_id=registered_model["id"],
        prompt_version_id=prompt["id"],
        tool_version_ids=[tool_version["id"]],
    )
    await _publish(api_client, admin_headers, agent["id"], version["id"])

    revoked = await api_client.delete(
        f"/api/v1/tools/{tool_version['tool_id']}/grant", headers=admin_headers
    )
    assert revoked.status_code == 204, revoked.text

    blocked = await api_client.post(
        f"/api/v1/agents/{agent['id']}/invoke",
        headers=admin_headers,
        json={"env": PROD, "input": {}},
    )
    assert blocked.status_code == 409, blocked.text
    error = blocked.json()["error"]
    assert error["code"] == "RESOURCE_UNAVAILABLE"
    assert any(
        detail.get("resource_id") == tool_version["id"] for detail in error["details"]
    )
    # 受理阶段即阻断，不产生运行记录
    assert await _count_runs(tenant_env.tenant_id) == 0


# --------------------------------------------------------------------- 8.4


async def test_audit_trail_covers_publish_rollback_grant_and_api_key(
    api_client: httpx.AsyncClient,
    tenant_env: TenantEnv,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_model(monkeypatch, RecordingModel())
    registered_model = await create_model(api_client, admin_headers)
    prompt = await _create_prompt(api_client, admin_headers, text="You are audited.")
    agent = await create_agent(api_client, admin_headers)
    version_v1 = await create_agent_version(
        api_client,
        admin_headers,
        agent["id"],
        model_id=registered_model["id"],
        prompt_version_id=prompt["id"],
    )
    release_v1 = await _publish(api_client, admin_headers, agent["id"], version_v1["id"])
    prompt_v2 = await _add_prompt_version(
        api_client, admin_headers, prompt["prompt_id"], text="You are audited v2."
    )
    version_v2 = await create_agent_version(
        api_client,
        admin_headers,
        agent["id"],
        model_id=registered_model["id"],
        prompt_version_id=prompt_v2["id"],
    )
    await _publish(api_client, admin_headers, agent["id"], version_v2["id"])
    rolled = await api_client.post(
        f"/api/v1/agents/{agent['id']}/rollback",
        headers=admin_headers,
        json={"env": PROD, "release_id": release_v1["release_id"]},
    )
    assert rolled.status_code == 200, rolled.text

    grant = await api_client.post(
        "/api/v1/grants",
        headers=admin_headers,
        json={
            "resource_type": "agent",
            "resource_id": agent["id"],
            "subject_type": "USER",
            "subject_id": str(tenant_env.user_id),
            "actions": ["agent.invoke"],
        },
    )
    assert grant.status_code == 201, grant.text
    revoked = await api_client.delete(
        f"/api/v1/grants/{grant.json()['id']}", headers=admin_headers
    )
    assert revoked.status_code == 204, revoked.text

    key = await api_client.post(
        "/api/v1/api-keys",
        headers=admin_headers,
        json={"name": "audited-key", "scopes": ["run:read"]},
    )
    assert key.status_code == 201, key.text
    disabled = await api_client.delete(f"/api/v1/api-keys/{key.json()['id']}", headers=admin_headers)
    assert disabled.status_code == 204, disabled.text

    agent_logs = await api_client.get(
        "/api/v1/audit-logs",
        headers=admin_headers,
        params={"resource_type": "agent", "resource_id": agent["id"], "limit": 50},
    )
    assert agent_logs.status_code == 200, agent_logs.text
    page = agent_logs.json()
    actions = [item["action"] for item in page["items"]]
    assert sorted(actions) == sorted(
        [
            "agent.release.published",
            "agent.release.published",
            "agent.release.rolled_back",
            "grant.created",
            "grant.revoked",
        ]
    )
    assert page["total"] == 5
    for item in page["items"]:
        assert item["result"] == "SUCCESS"
        assert item["actor_id"] == str(tenant_env.user_id)
        assert item["resource_id"] == agent["id"]

    key_logs = await api_client.get(
        "/api/v1/audit-logs",
        headers=admin_headers,
        params={"resource_type": "api_key", "resource_id": key.json()["id"]},
    )
    assert key_logs.status_code == 200, key_logs.text
    key_page = key_logs.json()
    assert key_page["total"] == 2
    assert {item["action"] for item in key_page["items"]} == {
        "api_key.created",
        "api_key.disabled",
    }
