"""端到端验收：工具从注册、版本、授权、测试调用，到 Agent 真实调用的概览闭环。"""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx
import pytest

from agent_platform.contexts.catalog.application.tool_service import tool_service
from agent_platform.contexts.execution.application.invocation_service import invocation_service
from agent_platform.contexts.execution.domain.ports import (
    ModelRequest,
    ModelResponse,
)
from agent_platform.contexts.execution.domain.ports import (
    ToolCall as ModelToolCall,
)
from tests.integration.agent_helpers import (
    create_agent,
    create_agent_version,
    create_model,
    create_prompt_version,
)
from tests.integration.conftest import TenantEnv

PROD = "PROD"


def _mock_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content) if request.content else {}
        return httpx.Response(
            200,
            json={"ok": True, "echo": payload, "path": request.url.path},
            headers={"content-type": "application/json"},
        )

    return httpx.MockTransport(handler)


def _spec(path: str, *, risk: str) -> dict:
    return {
        "method": "POST",
        "url": f"https://sandbox.local{path}",
        "timeout_s": 5,
        "headers": {"X-Env": "e2e"},
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    }


class ToolThenAnswerModel:
    """首次返回工具调用，收到工具结果后作答。"""

    def __init__(self, *, tool_code: str) -> None:
        self.tool_code = tool_code
        self.calls = 0

    async def chat(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        if not any(message.role == "tool" for message in request.messages):
            return ModelResponse(
                content="",
                tool_calls=(
                    ModelToolCall(
                        id="call-console-1", name=self.tool_code, arguments={"text": "hello"}
                    ),
                ),
            )
        return ModelResponse(content="工具已返回", input_tokens=6, output_tokens=3)


async def _publish(
    api_client: httpx.AsyncClient, headers: dict[str, str], agent_id: str, version_id: str
) -> None:
    response = await api_client.post(
        f"/api/v1/agents/{agent_id}/releases",
        headers=headers,
        json={"agent_version_id": version_id, "env": PROD},
    )
    assert response.status_code == 201, response.text


async def test_tool_console_flow(
    api_client: httpx.AsyncClient,
    tenant_env: TenantEnv,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tool_service, "_transport", _mock_transport())

    code = f"e2e-tool-{uuid.uuid4().hex[:6]}"
    created = await api_client.post(
        "/api/v1/tools",
        headers=admin_headers,
        json={
            "code": code,
            "name": "E2E Echo",
            "description": "端到端替身工具",
            "risk_level": "WRITE",
            "spec": _spec("/echo", risk="WRITE"),
        },
    )
    assert created.status_code == 201, created.text
    tool = created.json()
    assert tool["risk_level"] == "WRITE"
    assert tool["latest_version_no"] == 1
    assert tool["granted"] is False
    assert tool["status"] == "ACTIVE"

    listed = await api_client.get("/api/v1/tools", headers=admin_headers)
    assert listed.status_code == 200
    entry = next(item for item in listed.json() if item["id"] == tool["id"])
    assert entry["latest_version_no"] == 1
    assert entry["risk_level"] == "WRITE"

    # 应用新版本：改地址并降为只读，列表与详情以最新版本为准
    version = await api_client.post(
        f"/api/v1/tools/{tool['id']}/versions",
        headers=admin_headers,
        json={"spec": _spec("/echo-v2", risk="READ_ONLY"), "risk_level": "READ_ONLY"},
    )
    assert version.status_code == 201, version.text
    version_id = version.json()["id"]
    assert version.json()["version_no"] == 2

    detail = await api_client.get(f"/api/v1/tools/{tool['id']}", headers=admin_headers)
    assert detail.json()["latest_version_no"] == 2
    assert detail.json()["risk_level"] == "READ_ONLY"

    # 未授权时拒绝测试调用；授权后放行
    denied = await api_client.post(
        f"/api/v1/tool-versions/{version_id}/execute",
        headers=admin_headers,
        json={"arguments": {"text": "hello"}},
    )
    assert denied.status_code == 409, denied.text
    assert denied.json()["error"]["code"] == "RESOURCE_UNAVAILABLE"

    assert (
        await api_client.post(f"/api/v1/tools/{tool['id']}/grant", headers=admin_headers)
    ).status_code == 204
    grant = await api_client.get(f"/api/v1/tools/{tool['id']}/grant", headers=admin_headers)
    assert grant.json()["granted"] is True
    assert grant.json()["granted_by"] == str(tenant_env.user_id)

    executed = await api_client.post(
        f"/api/v1/tool-versions/{version_id}/execute",
        headers=admin_headers,
        json={"arguments": {"text": "hello"}},
    )
    assert executed.status_code == 200, executed.text
    result = executed.json()
    assert result["status"] == "SUCCESS"
    assert result["output"]["echo"] == {"text": "hello"}
    assert result["output"]["path"] == "/echo-v2"
    assert result["latency_ms"] >= 0
    assert len(result["request_digest"]) == 64

    # 测试调用不进入调用记录
    empty_overview = await api_client.get(
        "/api/v1/tool-calls", headers=admin_headers, params={"tool_id": tool["id"]}
    )
    assert empty_overview.status_code == 200
    assert empty_overview.json()["total"] == 0
    assert empty_overview.json()["success_rate"] is None

    # 停用后测试调用被拒绝，启用后恢复
    disabled = await api_client.patch(
        f"/api/v1/tools/{tool['id']}/status",
        headers=admin_headers,
        json={"status": "DISABLED"},
    )
    assert disabled.status_code == 200
    assert disabled.json()["status"] == "DISABLED"
    blocked = await api_client.post(
        f"/api/v1/tool-versions/{version_id}/execute",
        headers=admin_headers,
        json={"arguments": {"text": "hello"}},
    )
    assert blocked.status_code == 409, blocked.text

    enabled = await api_client.patch(
        f"/api/v1/tools/{tool['id']}/status",
        headers=admin_headers,
        json={"status": "ACTIVE"},
    )
    assert enabled.status_code == 200

    # Agent 真实调用产生调用记录
    model = ToolThenAnswerModel(tool_code=code)

    async def factory(_actor: Any, _snapshot: dict[str, Any], _timeout_s: float) -> Any:
        return model

    monkeypatch.setattr(invocation_service, "_model_factory", factory)

    registered_model = await create_model(api_client, admin_headers)
    prompt = await create_prompt_version(api_client, admin_headers)
    agent = await create_agent(api_client, admin_headers)
    agent_version = await create_agent_version(
        api_client,
        admin_headers,
        agent["id"],
        model_id=registered_model["id"],
        prompt_version_id=prompt["id"],
        tool_version_ids=[version_id],
    )
    await _publish(api_client, admin_headers, agent["id"], agent_version["id"])

    invoked = await api_client.post(
        f"/api/v1/agents/{agent['id']}/invoke",
        headers=admin_headers,
        json={"env": PROD, "input": {}},
    )
    assert invoked.status_code == 200, invoked.text
    assert invoked.json()["status"] == "SUCCESS", invoked.text
    assert model.calls == 2

    overview = await api_client.get(
        "/api/v1/tool-calls", headers=admin_headers, params={"tool_id": tool["id"]}
    )
    assert overview.status_code == 200, overview.text
    body = overview.json()
    assert body["total"] == 1
    assert body["succeeded"] == 1
    assert body["failed"] == 0
    assert body["success_rate"] == 1.0
    assert body["avg_latency_ms"] is not None
    assert body["errors"] == []
    assert body["items"][0]["status"] == "SUCCESS"
    assert body["items"][0]["tool_version_id"] == version_id
    assert "request_body" not in overview.text
    assert '"hello"' not in overview.text
