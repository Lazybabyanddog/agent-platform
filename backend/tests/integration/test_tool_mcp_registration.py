"""工具注册接口：接入类型可选，MCP 与 HTTP 分别按自己的契约校验。"""

from __future__ import annotations

import uuid

import pytest

from tests.integration.conftest import TenantEnv

MCP_SPEC = {
    "transport": "streamable_http",
    "url": "https://knowledge.example.com/mcp",
    "tool_name": "search_documents",
    "timeout_s": 10,
    "input_schema": {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    },
}

HTTP_SPEC = {
    "method": "POST",
    "url": "https://tools.local/echo",
    "timeout_s": 5,
}


def _payload(*, spec: dict, type: str | None = None) -> dict:
    body = {
        "code": f"tool-{uuid.uuid4().hex[:8]}",
        "name": "Registered",
        "description": "registered tool",
        "spec": spec,
    }
    if type is not None:
        body["type"] = type
    return body


async def test_mcp_tool_is_created_and_read_back_as_mcp(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str]
) -> None:
    created = await api_client.post(
        "/api/v1/tools", headers=admin_headers, json=_payload(spec=MCP_SPEC, type="MCP")
    )
    assert created.status_code == 201, created.text
    assert created.json()["type"] == "MCP"

    detail = await api_client.get(f"/api/v1/tools/{created.json()['id']}", headers=admin_headers)
    assert detail.status_code == 200
    assert detail.json()["type"] == "MCP"

    listed = await api_client.get("/api/v1/tools", headers=admin_headers)
    entry = next(item for item in listed.json() if item["id"] == created.json()["id"])
    assert entry["type"] == "MCP"

    versions = await api_client.get(
        f"/api/v1/tools/{created.json()['id']}/versions", headers=admin_headers
    )
    assert versions.status_code == 200
    spec = versions.json()[0]["spec"]
    assert spec["tool_name"] == "search_documents"
    assert spec["transport"] == "streamable_http"
    assert spec["timeout_s"] == 10.0


async def test_tool_without_type_stays_http(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str]
) -> None:
    created = await api_client.post(
        "/api/v1/tools", headers=admin_headers, json=_payload(spec=HTTP_SPEC)
    )
    assert created.status_code == 201, created.text
    assert created.json()["type"] == "HTTP"


async def test_unknown_type_is_rejected(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str]
) -> None:
    response = await api_client.post(
        "/api/v1/tools", headers=admin_headers, json=_payload(spec=HTTP_SPEC, type="GRPC")
    )
    assert response.status_code == 422, response.text


@pytest.mark.parametrize(
    ("spec", "field"),
    [
        ({**MCP_SPEC, "tool_name": ""}, "spec.tool_name"),
        ({**MCP_SPEC, "transport": "stdio"}, "spec.transport"),
        ({**MCP_SPEC, "url": "knowledge.example.com/mcp"}, "spec.url"),
    ],
)
async def test_invalid_mcp_spec_is_rejected_on_create(
    api_client,
    tenant_env: TenantEnv,
    admin_headers: dict[str, str],
    spec: dict,
    field: str,
) -> None:
    response = await api_client.post(
        "/api/v1/tools", headers=admin_headers, json=_payload(spec=spec, type="MCP")
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"][0]["field"] == field


async def test_http_tool_version_validation_is_unchanged(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str]
) -> None:
    created = await api_client.post(
        "/api/v1/tools", headers=admin_headers, json=_payload(spec=HTTP_SPEC)
    )
    assert created.status_code == 201, created.text
    tool_id = created.json()["id"]

    accepted = await api_client.post(
        f"/api/v1/tools/{tool_id}/versions",
        headers=admin_headers,
        json={"spec": {**HTTP_SPEC, "url": "https://tools.local/v2"}, "risk_level": "WRITE"},
    )
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["version_no"] == 2

    rejected = await api_client.post(
        f"/api/v1/tools/{tool_id}/versions",
        headers=admin_headers,
        json={"spec": {**HTTP_SPEC, "url": "tools.local/v3"}},
    )
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["error"]["details"][0]["field"] == "spec.url"


async def test_mcp_tool_version_is_validated_against_mcp_contract(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str]
) -> None:
    created = await api_client.post(
        "/api/v1/tools", headers=admin_headers, json=_payload(spec=MCP_SPEC, type="MCP")
    )
    assert created.status_code == 201, created.text
    tool_id = created.json()["id"]

    # 同一工具新增版本仍按 MCP 契约校验：缺 tool_name 被拒
    rejected = await api_client.post(
        f"/api/v1/tools/{tool_id}/versions",
        headers=admin_headers,
        json={"spec": {"transport": "sse", "url": "https://knowledge.example.com/sse"}},
    )
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["error"]["details"][0]["field"] == "spec.tool_name"

    accepted = await api_client.post(
        f"/api/v1/tools/{tool_id}/versions",
        headers=admin_headers,
        json={
            "spec": {
                "transport": "sse",
                "url": "https://knowledge.example.com/sse",
                "tool_name": "search_documents_v2",
            }
        },
    )
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["spec"]["transport"] == "sse"
