"""供应商与模型的 PATCH 维护能力：字段更新、审计与不可变字段拒绝。"""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from agent_platform.shared.db import system_session_scope
from tests.integration.conftest import TenantEnv


async def _latest_audit(tenant_id: uuid.UUID, action: str) -> dict[str, Any]:
    async with system_session_scope() as session:
        detail = await session.scalar(
            text(
                "select detail from platform.audit_log "
                "where tenant_id = :tid and action = :action "
                "order by created_at desc limit 1"
            ),
            {"tid": tenant_id, "action": action},
        )
    assert detail is not None, f"no audit entry for {action}"
    return detail


async def _create_provider(
    api_client: httpx.AsyncClient,
    headers: dict[str, str],
    *,
    name: str = "provider",
    base_url: str = "https://upstream.example/v1",
    region: str | None = None,
    secret_id: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"name": name, "base_url": base_url}
    if region is not None:
        payload["region"] = region
    if secret_id is not None:
        payload["secret_id"] = secret_id
    response = await api_client.post("/api/v1/model-providers", headers=headers, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def _create_model(
    api_client: httpx.AsyncClient,
    headers: dict[str, str],
    provider_id: str,
    *,
    param_template: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response = await api_client.post(
        "/api/v1/models",
        headers=headers,
        json={
            "provider_id": provider_id,
            "code": f"m-{uuid.uuid4().hex[:8]}",
            "model_name": "gpt-4o-mini",
            "ctx_window": 128000,
            "param_template": param_template
            or {"temperature": {"type": "number", "min": 0, "max": 2, "default": 0.7}},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_provider_update_saves_fields_and_writes_audit(
    api_client: httpx.AsyncClient, admin_headers: dict[str, str], tenant_env: TenantEnv
) -> None:
    provider = await _create_provider(api_client, admin_headers, region="cn-north")
    assert provider["region"] == "cn-north"

    response = await api_client.patch(
        f"/api/v1/model-providers/{provider['id']}",
        headers=admin_headers,
        json={"name": "renamed", "region": "cn-south"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] == "renamed"
    assert body["region"] == "cn-south"
    assert body["impact_notice"] is None

    listed = await api_client.get("/api/v1/model-providers", headers=admin_headers)
    assert any(item["id"] == provider["id"] and item["name"] == "renamed" for item in listed.json())

    detail = await _latest_audit(tenant_env.tenant_id, "provider.update")
    assert detail["changes"]["name"] == {"old": "provider", "new": "renamed"}
    assert detail["changes"]["region"] == {"old": "cn-north", "new": "cn-south"}


async def test_provider_base_url_change_declares_impact(
    api_client: httpx.AsyncClient, admin_headers: dict[str, str], tenant_env: TenantEnv
) -> None:
    provider = await _create_provider(api_client, admin_headers)

    response = await api_client.patch(
        f"/api/v1/model-providers/{provider['id']}",
        headers=admin_headers,
        json={"base_url": "https://upstream2.example/v1/"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["base_url"] == "https://upstream2.example/v1"
    assert body["impact_notice"] and "已发布" in body["impact_notice"]

    detail = await _latest_audit(tenant_env.tenant_id, "provider.update")
    assert detail["changes"]["base_url"] == {
        "old": "https://upstream.example/v1",
        "new": "https://upstream2.example/v1",
    }


async def test_provider_secret_update_audit_has_no_plaintext(
    api_client: httpx.AsyncClient, admin_headers: dict[str, str], tenant_env: TenantEnv
) -> None:
    plaintext = f"sk-live-{uuid.uuid4().hex[:8]}"
    secret = await api_client.post(
        "/api/v1/secrets",
        headers=admin_headers,
        json={"name": f"k-{uuid.uuid4().hex[:6]}", "value": plaintext},
    )
    assert secret.status_code == 201, secret.text
    provider = await _create_provider(api_client, admin_headers)

    response = await api_client.patch(
        f"/api/v1/model-providers/{provider['id']}",
        headers=admin_headers,
        json={"secret_id": secret.json()["id"]},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["secret_ref_id"] == secret.json()["id"]
    assert body["impact_notice"] is not None
    assert plaintext not in response.text

    detail = await _latest_audit(tenant_env.tenant_id, "provider.update")
    assert detail["changes"]["secret_ref_id"] == {"old": None, "new": secret.json()["id"]}
    assert plaintext not in json.dumps(detail)


async def test_disabled_provider_rejects_new_model(
    api_client: httpx.AsyncClient, admin_headers: dict[str, str]
) -> None:
    provider = await _create_provider(api_client, admin_headers)
    response = await api_client.patch(
        f"/api/v1/model-providers/{provider['id']}",
        headers=admin_headers,
        json={"status": "DISABLED"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "DISABLED"

    created = await api_client.post(
        "/api/v1/models",
        headers=admin_headers,
        json={
            "provider_id": provider["id"],
            "code": f"m-{uuid.uuid4().hex[:8]}",
            "model_name": "gpt-4o-mini",
        },
    )
    assert created.status_code == 409, created.text
    assert created.json()["error"]["code"] == "RESOURCE_UNAVAILABLE"


async def test_model_update_saves_fields_and_writes_audit(
    api_client: httpx.AsyncClient, admin_headers: dict[str, str], tenant_env: TenantEnv
) -> None:
    provider = await _create_provider(api_client, admin_headers)
    model = await _create_model(api_client, admin_headers, provider["id"])
    model_id = model["id"]

    response = await api_client.patch(
        f"/api/v1/models/{model_id}",
        headers=admin_headers,
        json={
            "model_name": "gpt-4o",
            "ctx_window": 64000,
            "param_template": {
                "temperature": {"type": "number", "min": 0, "max": 2, "default": 0.5},
                "top_p": {"type": "number", "min": 0, "max": 1, "default": 0.9},
            },
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["model_name"] == "gpt-4o"
    assert body["ctx_window"] == 64000
    assert body["code"] == model["code"]
    assert body["provider_id"] == provider["id"]

    detail = await _latest_audit(tenant_env.tenant_id, "model.update")
    assert detail["changes"]["model_name"] == {"old": "gpt-4o-mini", "new": "gpt-4o"}
    template_diff = detail["changes"]["param_template"]
    assert template_diff["keys_added"] == ["top_p"]
    assert template_diff["keys_changed"] == ["temperature"]
    assert "keys_added" in template_diff and "before_keys" in template_diff


@pytest.mark.parametrize("field", ["code", "provider_id"])
async def test_model_update_rejects_immutable_fields(
    field: str, api_client: httpx.AsyncClient, admin_headers: dict[str, str]
) -> None:
    provider = await _create_provider(api_client, admin_headers)
    model = await _create_model(api_client, admin_headers, provider["id"])
    value = f"m-{uuid.uuid4().hex[:8]}" if field == "code" else str(uuid.uuid4())

    response = await api_client.patch(
        f"/api/v1/models/{model['id']}", headers=admin_headers, json={field: value}
    )
    assert response.status_code == 422, response.text
    details = response.json()["error"]["details"]
    assert details[0]["field"] == field
    assert "immutable" in details[0]["reason"]


async def test_model_update_rejects_unsupported_param_key(
    api_client: httpx.AsyncClient, admin_headers: dict[str, str]
) -> None:
    provider = await _create_provider(api_client, admin_headers)
    model = await _create_model(api_client, admin_headers, provider["id"])

    response = await api_client.patch(
        f"/api/v1/models/{model['id']}",
        headers=admin_headers,
        json={"param_template": {"reasoning_effort": {"type": "string"}}},
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"][0]["field"] == "param_template.reasoning_effort"
