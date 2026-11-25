"""追踪正文落库、敏感字段掩码与正文读取权限。"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from agent_platform.contexts.execution.application.invocation_service import invocation_service
from agent_platform.contexts.execution.domain.ports import (
    ModelRequest,
    ModelResponse,
    ToolCall,
    ToolResult,
)
from agent_platform.contexts.insight.infrastructure.tables import ToolCall as ToolCallRow
from agent_platform.shared.db import session_scope
from tests.integration.agent_helpers import (
    create_agent,
    create_agent_version,
    create_model,
    create_prompt_version,
    suffix,
)
from tests.integration.conftest import TenantEnv

SENSITIVE_TOOL_SPEC = {
    "method": "POST",
    "url": "https://tools.local/profile",
    "timeout_s": 5,
    "input_schema": {
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "password": {"type": "string", "x-sensitive": True},
        },
        "required": ["text"],
    },
    "output_schema": {
        "type": "object",
        "properties": {"ok": {"type": "boolean"}},
        "required": ["ok"],
    },
}


class ToolCallingModel:
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
                    ToolCall(
                        id="call-1",
                        name=self.tool_code,
                        arguments={"text": "hi", "password": "s3cret"},
                    ),
                ),
            )
        return ModelResponse(content="done")


class EchoTool:
    async def invoke(self, *, tool_code: str, tool_version_id: str, arguments):
        del tool_code, tool_version_id
        return ToolResult(
            ok=True,
            content=json.dumps({"ok": True, "secret_token": "abc", "echo": arguments["text"]}),
        )


def use_model(monkeypatch: pytest.MonkeyPatch, model) -> None:
    async def factory(actor, snapshot, timeout_s):
        return model

    monkeypatch.setattr(invocation_service, "_model_factory", factory)


def use_tool(monkeypatch: pytest.MonkeyPatch, tool) -> None:
    monkeypatch.setattr(invocation_service, "_tool_factory", lambda actor: tool)


async def _create_sensitive_tool(api_client, headers: dict[str, str]) -> dict:
    created = await api_client.post(
        "/api/v1/tools",
        headers=headers,
        json={
            "code": f"tool-{suffix()}",
            "name": "Profile",
            "description": "profile",
            "spec": SENSITIVE_TOOL_SPEC,
        },
    )
    assert created.status_code == 201, created.text
    tool_id = created.json()["id"]
    granted = await api_client.post(f"/api/v1/tools/{tool_id}/grant", headers=headers)
    assert granted.status_code == 204, granted.text
    versions = await api_client.get(f"/api/v1/tools/{tool_id}/versions", headers=headers)
    return {**versions.json()[0], "tool_code": created.json()["code"]}


async def _published_agent(api_client, headers: dict[str, str], tool: dict) -> dict:
    model = await create_model(api_client, headers)
    prompt = await create_prompt_version(api_client, headers)
    agent = await create_agent(api_client, headers)
    version = await create_agent_version(
        api_client,
        headers,
        agent["id"],
        model_id=model["id"],
        prompt_version_id=prompt["id"],
        tool_version_ids=[tool["id"]],
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
    assert response.json()["status"] == "SUCCESS", response.text
    return response.json()


def _tool_entry(trace: dict) -> dict:
    return next(entry for entry in trace["entries"] if entry["type"] == "TOOL_CALL")


async def test_tool_call_stores_masked_body_by_default(
    api_client,
    tenant_env: TenantEnv,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool = await _create_sensitive_tool(api_client, admin_headers)
    agent = await _published_agent(api_client, admin_headers, tool)
    use_model(monkeypatch, ToolCallingModel(tool["tool_code"]))
    use_tool(monkeypatch, EchoTool())

    invoked = await _invoke(api_client, admin_headers, agent["id"])
    trace = await api_client.get(
        f"/api/v1/runs/{invoked['run_id']}/trace", headers=admin_headers
    )
    assert trace.status_code == 200, trace.text
    entry = _tool_entry(trace.json())
    assert entry["content"]["request"] == {"text": "hi", "password": "***"}
    assert entry["content"]["response"]["content"]["secret_token"] == "***"
    assert entry["content"]["response"]["content"]["echo"] == "hi"
    assert entry["summary"]["keys"] == ["password", "text"]

    async with session_scope(tenant_env.tenant_id) as session:
        stored = (
            await session.scalars(
                select(ToolCallRow).where(ToolCallRow.tenant_id == tenant_env.tenant_id)
            )
        ).one()
    assert stored.request_body == {"text": "hi", "password": "***"}
    assert stored.response_body["content"]["secret_token"] == "***"
    assert stored.request_summary["keys"] == ["password", "text"]
    assert "hi" not in json.dumps(stored.request_summary)


async def test_member_only_sees_summary(
    api_client,
    tenant_env: TenantEnv,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool = await _create_sensitive_tool(api_client, admin_headers)
    agent = await _published_agent(api_client, admin_headers, tool)
    use_model(monkeypatch, ToolCallingModel(tool["tool_code"]))
    use_tool(monkeypatch, EchoTool())
    invoked = await _invoke(api_client, admin_headers, agent["id"])

    member_email = f"member-{suffix()}@example.com"
    member_password = "Passw0rd!456"
    registered = await api_client.post(
        "/api/v1/auth/register",
        json={
            "tenant_code": tenant_env.tenant_code,
            "email": member_email,
            "password": member_password,
            "display_name": "Member",
        },
    )
    assert registered.status_code == 201, registered.text
    login = await api_client.post(
        "/api/v1/auth/login",
        json={
            "tenant_code": tenant_env.tenant_code,
            "email": member_email,
            "password": member_password,
        },
    )
    assert login.status_code == 200, login.text
    member_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    member_trace = await api_client.get(
        f"/api/v1/runs/{invoked['run_id']}/trace", headers=member_headers
    )
    assert member_trace.status_code == 200, member_trace.text
    member_entry = _tool_entry(member_trace.json())
    assert member_entry["content"] is None
    assert member_entry["summary"]["keys"] == ["password", "text"]

    # 留存开关端点已移除，旧路径不再存在
    legacy = await api_client.patch(
        f"/api/v1/tenants/{tenant_env.tenant_id}/insight-retention",
        headers=admin_headers,
        json={"enabled": True},
    )
    assert legacy.status_code in (404, 405), legacy.text
