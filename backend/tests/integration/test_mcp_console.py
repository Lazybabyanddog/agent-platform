"""MCP 控制台端到端：探测取契约 → 注册工具 → 保存版本 → 授权 → 绑定 → 发布 → 调用。

前置的 `test_tool_mcp_probe.py` 只验证探测端点本身；这里验证「按探测结果注册出来的
MCP 工具」确实能被 Agent 绑定并在轨迹里留下远端工具名与耗时。
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from agent_platform.contexts.execution.application.invocation_service import invocation_service
from agent_platform.contexts.execution.domain.ports import ModelRequest, ModelResponse, ToolCall
from agent_platform.contexts.insight.infrastructure.tables import ToolCall as ToolCallRow
from agent_platform.shared.db import session_scope
from tests.integration.agent_helpers import (
    create_agent,
    create_agent_version,
    create_model,
    create_prompt_version,
)
from tests.integration.conftest import TenantEnv
from tests.integration.mcp_server import local_mcp_server

ENV = "PROD"


class SearchThenAnswerModel:
    """首轮按工具编码调用 MCP 工具，拿到远端结果后原样作为最终答案。"""

    def __init__(self, *, tool_code: str) -> None:
        self.tool_code = tool_code

    async def chat(self, request: ModelRequest) -> ModelResponse:
        tool_messages = [message for message in request.messages if message.role == "tool"]
        if not tool_messages:
            return ModelResponse(
                content="",
                tool_calls=(
                    ToolCall(
                        id="call-console-1",
                        name=self.tool_code,
                        arguments={"query": "退款", "limit": 1},
                    ),
                ),
            )
        return ModelResponse(content=tool_messages[-1].content, input_tokens=8, output_tokens=4)


async def _probe(api_client: httpx.AsyncClient, headers: dict[str, str], url: str) -> dict[str, Any]:
    response = await api_client.post(
        "/api/v1/tools/probe-mcp",
        headers=headers,
        json={"transport": "streamable_http", "url": url, "timeout_s": 10},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["verdict"] == "OK", body
    assert [item["name"] for item in body["tools"]] == ["search_documents"]
    return body["tools"][0]


async def _register_from_probe(
    api_client: httpx.AsyncClient,
    headers: dict[str, str],
    *,
    url: str,
    remote: dict[str, Any],
) -> dict[str, Any]:
    """模拟控制台：用探测到的描述与入参 Schema 组装注册请求。"""
    created = await api_client.post(
        "/api/v1/tools",
        headers=headers,
        json={
            "code": f"mcp-console-{uuid.uuid4().hex[:6]}",
            "name": "Knowledge Search",
            "description": remote["description"],
            "type": "MCP",
            "risk_level": "READ_ONLY",
            "spec": {
                "transport": "streamable_http",
                "url": url,
                "tool_name": remote["name"],
                "timeout_s": 10,
                "headers": {},
                "auth": {"type": "none"},
                "input_schema": remote["input_schema"],
            },
        },
    )
    assert created.status_code == 201, created.text
    return created.json()


async def test_mcp_console_flow_registers_version_and_traces_remote_tool(
    api_client: httpx.AsyncClient,
    tenant_env: TenantEnv,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with local_mcp_server() as url:
        remote = await _probe(api_client, admin_headers, url)
        tool = await _register_from_probe(api_client, admin_headers, url=url, remote=remote)

        # 保存版本：MCP 契约按工具类型校验，新增版本仍是 MCP
        saved = await api_client.post(
            f"/api/v1/tools/{tool['id']}/versions",
            headers=admin_headers,
            json={
                "spec": {
                    "transport": "streamable_http",
                    "url": url,
                    "tool_name": remote["name"],
                    "timeout_s": 15,
                    "input_schema": remote["input_schema"],
                },
                "risk_level": "READ_ONLY",
            },
        )
        assert saved.status_code == 201, saved.text
        version = saved.json()
        assert version["version_no"] == 2
        assert version["spec"]["tool_name"] == "search_documents"

        granted = await api_client.post(f"/api/v1/tools/{tool['id']}/grant", headers=admin_headers)
        assert granted.status_code == 204, granted.text

        model = SearchThenAnswerModel(tool_code=tool["code"])

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
            tool_version_ids=[version["id"]],
        )
        released = await api_client.post(
            f"/api/v1/agents/{agent['id']}/releases",
            headers=admin_headers,
            json={"agent_version_id": agent_version["id"], "env": ENV},
        )
        assert released.status_code == 201, released.text

        invoked = await api_client.post(
            f"/api/v1/agents/{agent['id']}/invoke",
            headers={**admin_headers, "X-Trace-Id": "mcp-console-trace"},
            json={"env": ENV, "input": {}},
        )
        assert invoked.status_code == 200, invoked.text
        body = invoked.json()
        assert body["status"] == "SUCCESS", body

    assert json.loads(body["output"]) == {
        "query": "退款",
        "hits": 1,
        "documents": [{"id": "doc-1", "title": "退款政策", "score": 0.93}],
    }

    trace = await api_client.get(
        f"/api/v1/runs/{body['run_id']}/trace", headers=admin_headers
    )
    assert trace.status_code == 200, trace.text
    tool_entries = [
        entry for entry in trace.json()["entries"] if entry["type"] == "TOOL_CALL"
    ]
    assert len(tool_entries) == 1
    entry = tool_entries[0]
    assert entry["label"] == tool["code"]
    assert entry["status"] == "SUCCESS"
    assert entry["latency_ms"] >= 0
    assert entry["summary"]["response"]["remote_tool_name"] == "search_documents"

    async with session_scope(tenant_env.tenant_id) as session:
        rows = list(
            (
                await session.scalars(
                    select(ToolCallRow).where(ToolCallRow.run_id == uuid.UUID(body["run_id"]))
                )
            ).all()
        )
    assert len(rows) == 1
    record = rows[0]
    assert record.tool_code == tool["code"]
    assert record.tool_version_id == uuid.UUID(version["id"])
    assert record.response_summary["remote_tool_name"] == "search_documents"
