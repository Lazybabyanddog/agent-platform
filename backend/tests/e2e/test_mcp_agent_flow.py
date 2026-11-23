"""端到端验收：MCP 工具接入后，一次调用在轨迹中构成完整 LLM_CALL + TOOL_CALL。"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from agent_platform.contexts.execution.application.invocation_service import invocation_service
from agent_platform.contexts.execution.domain.ports import ModelRequest, ModelResponse, ToolCall
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


class McpAgentModel:
    """先发起一次 MCP 工具调用，再用工具结果作答。"""

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
                        id="call-e2e-1",
                        name=self.tool_code,
                        arguments={"query": "退款", "limit": 2},
                    ),
                ),
            )
        documents = json.loads(tool_messages[-1].content)["documents"]
        titles = "、".join(item["title"] for item in documents)
        return ModelResponse(content=f"根据知识库：{titles}", input_tokens=12, output_tokens=8)


async def _publish(
    api_client: httpx.AsyncClient, headers: dict[str, str], agent_id: str, version_id: str
) -> None:
    response = await api_client.post(
        f"/api/v1/agents/{agent_id}/releases",
        headers=headers,
        json={"agent_version_id": version_id, "env": PROD},
    )
    assert response.status_code == 201, response.text


async def test_mcp_tool_call_composes_trace_with_llm_span(
    api_client: httpx.AsyncClient,
    tenant_env: TenantEnv,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with local_mcp_server() as url:
        tool_version = await create_mcp_tool_version(api_client, admin_headers, url=url)
        model = McpAgentModel(tool_code=tool_version["tool_code"])

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
            headers={**admin_headers, "X-Trace-Id": "mcp-e2e-trace"},
            json={"env": PROD, "input": {}},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "SUCCESS", body
    assert body["output"] == "根据知识库：退款政策、发票开具说明"

    # 模型确实看到了远端 MCP 工具的返回内容
    assert len(model.requests) == 2
    tool_message = next(m for m in model.requests[1].messages if m.role == "tool")
    assert json.loads(tool_message.content)["hits"] == 2

    trace = await api_client.get(
        f"/api/v1/runs/{body['run_id']}/trace", headers=admin_headers
    )
    assert trace.status_code == 200, trace.text
    payload = trace.json()
    entries = payload["entries"]

    tool_entries = [entry for entry in entries if entry["type"] == "TOOL_CALL"]
    llm_entries = [entry for entry in entries if entry["type"] == "LLM_CALL"]
    # 一次带工具的调用 = 两次模型调用（发起工具 / 生成答案）+ 一次工具调用
    assert len(tool_entries) == 1
    assert len(llm_entries) == 2
    assert len(entries) == 3, entries

    tool_entry = tool_entries[0]
    assert tool_entry["label"] == tool_version["tool_code"]
    assert tool_entry["status"] == "SUCCESS"
    assert tool_entry["error_code"] is None
    assert tool_entry["latency_ms"] >= 0
    assert tool_entry["summary"]["response"]["remote_tool_name"] == "search_documents"
    assert tool_entry["trace_id"] == "mcp-e2e-trace"
    assert all(entry["trace_id"] == "mcp-e2e-trace" for entry in llm_entries)
    assert all(entry["status"] == "SUCCESS" for entry in llm_entries)
