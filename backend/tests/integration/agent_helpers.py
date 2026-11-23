"""agent 集成测试的公共构造器（非测试文件）。"""

from __future__ import annotations

import uuid
from typing import Any

import httpx

PARAM_TEMPLATE = {
    "temperature": {"type": "number", "min": 0, "max": 2, "default": 0.2},
    "max_tokens": {"type": "integer", "min": 1, "max": 32000, "default": 1024},
}

TOOL_SPEC = {
    "method": "POST",
    "url": "https://tools.local/echo",
    "timeout_s": 5,
    "input_schema": {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    },
    "output_schema": {
        "type": "object",
        "properties": {"ok": {"type": "boolean"}},
        "required": ["ok"],
    },
}


def suffix() -> str:
    return uuid.uuid4().hex[:6]


async def create_model(api_client: httpx.AsyncClient, headers: dict[str, str]) -> dict[str, Any]:
    provider = await api_client.post(
        "/api/v1/model-providers",
        headers=headers,
        json={"name": f"provider-{suffix()}", "base_url": "http://127.0.0.1:9/v1"},
    )
    assert provider.status_code == 201, provider.text
    response = await api_client.post(
        "/api/v1/models",
        headers=headers,
        json={
            "provider_id": provider.json()["id"],
            "code": f"chat-{suffix()}",
            "model_name": "gpt-4o-mini",
            "ctx_window": 128000,
            "param_template": PARAM_TEMPLATE,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def create_prompt_version(
    api_client: httpx.AsyncClient, headers: dict[str, str]
) -> dict[str, Any]:
    created = await api_client.post(
        "/api/v1/prompts",
        headers=headers,
        json={
            "code": f"prompt-{suffix()}",
            "name": "Support",
            "messages": [{"role": "system", "content": "You are a support agent."}],
            "variables": [],
        },
    )
    assert created.status_code == 201, created.text
    prompt_id = created.json()["id"]
    versions = await api_client.get(f"/api/v1/prompts/{prompt_id}/versions", headers=headers)
    assert versions.status_code == 200, versions.text
    return versions.json()[0]


async def add_prompt_version(
    api_client: httpx.AsyncClient, headers: dict[str, str], prompt_id: str
) -> dict[str, Any]:
    response = await api_client.post(
        f"/api/v1/prompts/{prompt_id}/versions",
        headers=headers,
        json={
            "messages": [{"role": "system", "content": "You are a support agent. Be brief."}],
            "variables": [],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def create_tool_version(
    api_client: httpx.AsyncClient, headers: dict[str, str], *, grant: bool = True
) -> dict[str, Any]:
    created = await api_client.post(
        "/api/v1/tools",
        headers=headers,
        json={
            "code": f"tool-{suffix()}",
            "name": "Echo",
            "description": "echo",
            "spec": TOOL_SPEC,
        },
    )
    assert created.status_code == 201, created.text
    tool_id = created.json()["id"]
    if grant:
        granted = await api_client.post(f"/api/v1/tools/{tool_id}/grant", headers=headers)
        assert granted.status_code == 204, granted.text
    versions = await api_client.get(f"/api/v1/tools/{tool_id}/versions", headers=headers)
    assert versions.status_code == 200, versions.text
    return {**versions.json()[0], "tool_code": created.json()["code"]}


MCP_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string"},
        "limit": {"type": "integer"},
    },
    "required": ["query"],
}

MCP_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "hits": {"type": "integer"},
        "documents": {"type": "array"},
    },
    "required": ["hits"],
}


async def create_mcp_tool_version(
    api_client: httpx.AsyncClient,
    headers: dict[str, str],
    *,
    url: str,
    tool_name: str = "search_documents",
    grant: bool = True,
) -> dict[str, Any]:
    created = await api_client.post(
        "/api/v1/tools",
        headers=headers,
        json={
            "code": f"mcp-{suffix()}",
            "name": "Knowledge Search",
            "description": "knowledge platform over mcp",
            "type": "MCP",
            "spec": {
                "transport": "streamable_http",
                "url": url,
                "tool_name": tool_name,
                "timeout_s": 10,
                "input_schema": MCP_INPUT_SCHEMA,
                "output_schema": MCP_OUTPUT_SCHEMA,
            },
        },
    )
    assert created.status_code == 201, created.text
    tool_id = created.json()["id"]
    if grant:
        granted = await api_client.post(f"/api/v1/tools/{tool_id}/grant", headers=headers)
        assert granted.status_code == 204, granted.text
    versions = await api_client.get(f"/api/v1/tools/{tool_id}/versions", headers=headers)
    assert versions.status_code == 200, versions.text
    return {**versions.json()[0], "tool_code": created.json()["code"]}


async def add_tool_version(
    api_client: httpx.AsyncClient, headers: dict[str, str], tool_id: str
) -> dict[str, Any]:
    response = await api_client.post(
        f"/api/v1/tools/{tool_id}/versions",
        headers=headers,
        json={"spec": TOOL_SPEC},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def create_agent(api_client: httpx.AsyncClient, headers: dict[str, str]) -> dict[str, Any]:
    response = await api_client.post(
        "/api/v1/agents",
        headers=headers,
        json={"code": f"agent-{suffix()}", "name": "Support Agent"},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def create_agent_version(
    api_client: httpx.AsyncClient,
    headers: dict[str, str],
    agent_id: str,
    *,
    model_id: str,
    prompt_version_id: str,
    tool_version_ids: list[str] | None = None,
    params: dict[str, Any] | None = None,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response = await api_client.post(
        f"/api/v1/agents/{agent_id}/versions",
        headers=headers,
        json={
            "model_id": model_id,
            "prompt_version_id": prompt_version_id,
            "tool_version_ids": tool_version_ids or [],
            "params": params or {},
            "policy": policy or {},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()
