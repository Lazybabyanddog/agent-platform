"""真实协议验证：起本地 MCP server，按 MCP 注册工具、授权并真实调用一次。"""

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
    create_mcp_tool_version,
    create_model,
    create_prompt_version,
)
from tests.integration.conftest import TenantEnv
from tests.integration.mcp_server import local_mcp_server

PROD = "PROD"


class McpToolModel:
    """首轮请求 MCP 工具，拿到工具结果后原样作为最终答案。"""

    def __init__(self, *, tool_code: str) -> None:
        self.tool_code = tool_code
        self.requests: list[ModelRequest] = []

    async def chat(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        tool_messages = [message for message in request.messages if message.role == "tool"]
        if not tool_messages:
            return ModelResponse(
                content="",
                tool_calls=(
                    ToolCall(
                        id="call-mcp-1",
                        name=self.tool_code,
                        arguments={"query": "退款", "limit": 1},
                    ),
                ),
            )
        return ModelResponse(content=tool_messages[-1].content, input_tokens=8, output_tokens=4)


async def _publish(
    api_client: httpx.AsyncClient, headers: dict[str, str], agent_id: str, version_id: str
) -> dict[str, Any]:
    response = await api_client.post(
        f"/api/v1/agents/{agent_id}/releases",
        headers=headers,
        json={"agent_version_id": version_id, "env": PROD},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _tool_call_rows(tenant_id: uuid.UUID, run_id: uuid.UUID) -> list[ToolCallRow]:
    async with session_scope(tenant_id) as session:
        return list(
            (
                await session.scalars(
                    select(ToolCallRow).where(ToolCallRow.run_id == run_id)
                )
            ).all()
        )


async def test_live_mcp_call_returns_server_payload_and_is_traceable(
    api_client: httpx.AsyncClient,
    tenant_env: TenantEnv,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with local_mcp_server() as url:
        tool_version = await create_mcp_tool_version(api_client, admin_headers, url=url)
        model = McpToolModel(tool_code=tool_version["tool_code"])

        async def factory(_actor: Any, _snapshot: dict[str, Any], _timeout_s: float) -> Any:
            return model

        monkeypatch.setattr(invocation_service, "_model_factory", factory)

        registered_model = await create_model(api_client, admin_headers)
        prompt = await create_prompt_version(api_client, admin_headers)
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

        response = await api_client.post(
            f"/api/v1/agents/{agent['id']}/invoke",
            headers={**admin_headers, "X-Trace-Id": "mcp-live-trace"},
            json={"env": PROD, "input": {}},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "SUCCESS", body

    # 结果来自本地 MCP server 的确定性返回，说明协议链路真的打通了
    assert json.loads(body["output"]) == {
        "query": "退款",
        "hits": 1,
        "documents": [{"id": "doc-1", "title": "退款政策", "score": 0.93}],
    }

    rows = await _tool_call_rows(tenant_env.tenant_id, uuid.UUID(body["run_id"]))
    assert len(rows) == 1
    record = rows[0]
    assert record.tool_code == tool_version["tool_code"]
    assert record.status == "SUCCESS"
    assert record.latency_ms >= 0
    assert record.trace_id == "mcp-live-trace"
    assert record.response_summary["remote_tool_name"] == "search_documents"
