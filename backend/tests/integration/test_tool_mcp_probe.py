"""MCP 探测端点：对真实 streamable_http server 取回契约，且不留下注册痕迹。"""

from __future__ import annotations

import uuid

import httpx
from sqlalchemy import func, select

from agent_platform.contexts.catalog.infrastructure.tables import Tool, ToolVersion
from agent_platform.shared.db import session_scope
from tests.integration.conftest import TenantEnv
from tests.integration.mcp_server import local_mcp_server

PROBE_URL = "/api/v1/tools/probe-mcp"


async def _counts(tenant_id: uuid.UUID) -> tuple[int, int]:
    async with session_scope(tenant_id) as session:
        tools = await session.scalar(select(func.count()).select_from(Tool))
        versions = await session.scalar(select(func.count()).select_from(ToolVersion))
    return int(tools or 0), int(versions or 0)


async def test_probe_returns_remote_tool_contract(
    api_client: httpx.AsyncClient, tenant_env: TenantEnv, admin_headers: dict[str, str]
) -> None:
    with local_mcp_server() as url:
        before = await _counts(tenant_env.tenant_id)
        response = await api_client.post(
            PROBE_URL,
            headers=admin_headers,
            json={"transport": "streamable_http", "url": url, "timeout_s": 10},
        )
        after = await _counts(tenant_env.tenant_id)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["verdict"] == "OK"
    assert body["error_summary"] is None
    assert body["latency_ms"] >= 0

    assert [tool["name"] for tool in body["tools"]] == ["search_documents"]
    remote = body["tools"][0]
    assert remote["description"] == "按关键词检索知识库文档。"
    assert remote["input_schema"]["type"] == "object"
    assert set(remote["input_schema"]["properties"]) == {"query", "limit"}

    # 探测只读：不在注册表留下工具或版本
    assert after == before


async def test_probe_reports_unreachable_endpoint(
    api_client: httpx.AsyncClient, admin_headers: dict[str, str]
) -> None:
    response = await api_client.post(
        PROBE_URL,
        headers=admin_headers,
        json={"transport": "streamable_http", "url": "http://127.0.0.1:1/mcp", "timeout_s": 3},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["verdict"] == "MCP_PROBE_UNREACHABLE"
    assert body["tools"] == []
    assert body["error_summary"]


async def test_probe_rejects_stdio_transport(
    api_client: httpx.AsyncClient, admin_headers: dict[str, str]
) -> None:
    response = await api_client.post(
        PROBE_URL,
        headers=admin_headers,
        json={"transport": "stdio", "url": "http://127.0.0.1:1/mcp", "timeout_s": 3},
    )

    assert response.status_code == 422, response.text
    assert "transport" in response.text
