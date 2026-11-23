"""探测落库、审计与历史接口。"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from agent_platform.contexts.catalog.application.model_service import model_service
from agent_platform.shared.db import system_session_scope
from tests.integration.conftest import TenantEnv


async def _create_target(
    api_client: httpx.AsyncClient, headers: dict[str, str]
) -> tuple[dict[str, Any], dict[str, Any]]:
    provider = await api_client.post(
        "/api/v1/model-providers",
        headers=headers,
        json={"name": "probe-provider", "base_url": "https://stub.local/v1"},
    )
    assert provider.status_code == 201, provider.text
    model = await api_client.post(
        "/api/v1/models",
        headers=headers,
        json={
            "provider_id": provider.json()["id"],
            "code": f"probe-{uuid.uuid4().hex[:8]}",
            "model_name": "gpt-4o-mini",
        },
    )
    assert model.status_code == 201, model.text
    return provider.json(), model.json()


def _transport(payload: Any, status_code: int = 200) -> httpx.MockTransport:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=payload)

    return httpx.MockTransport(handler)


@pytest.fixture()
def stub_transport(monkeypatch: pytest.MonkeyPatch) -> Any:
    def _install(payload: Any, status_code: int = 200) -> None:
        monkeypatch.setattr(
            model_service, "_transport", _transport(payload, status_code), raising=True
        )

    return _install


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
    assert detail is not None
    return detail


async def test_model_probe_is_persisted_and_audited(
    api_client: httpx.AsyncClient,
    admin_headers: dict[str, str],
    tenant_env: TenantEnv,
    stub_transport: Any,
) -> None:
    stub_transport({"data": [{"id": "gpt-4o-mini"}, {"id": "other"}]})
    _provider, model = await _create_target(api_client, admin_headers)

    response = await api_client.post(
        f"/api/v1/models/{model['id']}/probe", headers=admin_headers
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["verdict"] == "OK"
    assert body["observed_model_name"] == "gpt-4o-mini"
    assert body["probe_id"]

    history = await api_client.get(
        f"/api/v1/models/{model['id']}/probes", headers=admin_headers
    )
    assert history.status_code == 200, history.text
    records = history.json()
    assert len(records) == 1
    record = records[0]
    assert record["id"] == body["probe_id"]
    assert record["verdict"] == "OK"
    assert record["observed_models"] == ["gpt-4o-mini", "other"]
    assert record["latency_ms"] >= 0
    assert record["actor_id"] is not None

    detail = await _latest_audit(tenant_env.tenant_id, "model.probe")
    assert detail["verdict"] == "OK"
    assert "latency_ms" in detail


async def test_provider_probe_has_no_model_id(
    api_client: httpx.AsyncClient,
    admin_headers: dict[str, str],
    stub_transport: Any,
) -> None:
    stub_transport({"data": [{"id": "a"}]})
    provider, _model = await _create_target(api_client, admin_headers)

    response = await api_client.post(
        f"/api/v1/model-providers/{provider['id']}/probe", headers=admin_headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["verdict"] == "OK"

    history = await api_client.get(
        f"/api/v1/model-providers/{provider['id']}/probes", headers=admin_headers
    )
    assert history.status_code == 200, history.text
    records = history.json()
    assert [item["verdict"] for item in records] == ["OK"]

    async with system_session_scope() as session:
        model_ids = list(
            await session.scalars(
                text("select model_id from catalog.model_probe where provider_id = :pid"),
                {"pid": provider["id"]},
            )
        )
    assert model_ids == [None]


async def test_probe_history_is_newest_first_and_limited(
    api_client: httpx.AsyncClient,
    admin_headers: dict[str, str],
    stub_transport: Any,
) -> None:
    stub_transport({"data": [{"id": "gpt-4o-mini"}]})
    provider, model = await _create_target(api_client, admin_headers)

    for _ in range(3):
        response = await api_client.post(
            f"/api/v1/models/{model['id']}/probe", headers=admin_headers
        )
        assert response.status_code == 200, response.text
        await asyncio.sleep(0.01)

    history = await api_client.get(
        f"/api/v1/models/{model['id']}/probes?limit=2", headers=admin_headers
    )
    assert history.status_code == 200, history.text
    records = history.json()
    assert len(records) == 2
    assert records[0]["created_at"] >= records[1]["created_at"]

    provider_history = await api_client.get(
        f"/api/v1/model-providers/{provider['id']}/probes?limit=1", headers=admin_headers
    )
    assert provider_history.status_code == 200
    assert len(provider_history.json()) == 1
