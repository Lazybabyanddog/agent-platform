from __future__ import annotations

import httpx
import pytest
from pydantic import BaseModel

from agent_platform.main import create_app
from agent_platform.shared.errors import ValidationFailedError


class Payload(BaseModel):
    name: str
    count: int


@pytest.fixture()
def client_app():
    app = create_app()

    @app.get("/_test/validation")
    async def _validation() -> None:
        raise ValidationFailedError.for_field("model.temperature", "must be <= 2")

    @app.get("/_test/boom")
    async def _boom() -> None:
        raise RuntimeError("secret detail that must not leak")

    return app


@pytest.fixture()
async def client(client_app):
    transport = httpx.ASGITransport(app=client_app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def test_healthz_ok(client):
    response = await client.get("/healthz")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.headers["X-Trace-Id"]


async def test_trace_id_is_propagated(client):
    response = await client.get("/healthz", headers={"X-Trace-Id": "trace-abc-123"})

    assert response.headers["X-Trace-Id"] == "trace-abc-123"


async def test_unknown_route_uses_envelope(client):
    response = await client.get("/nope")
    body = response.json()

    assert response.status_code == 404
    assert body["error"]["code"] == "NOT_FOUND"
    assert body["error"]["trace_id"]


async def test_app_error_has_located_details(client):
    response = await client.get("/_test/validation")
    body = response.json()

    assert response.status_code == 422
    assert body["error"]["code"] == "VALIDATION_FAILED"
    assert body["error"]["details"][0]["field"] == "model.temperature"


async def test_unhandled_error_does_not_leak_details(client):
    response = await client.get("/_test/boom")
    body = response.json()

    assert response.status_code == 500
    assert body["error"]["code"] == "INTERNAL_ERROR"
    assert body["error"]["message"] == "internal error"
    assert "secret detail" not in response.text


async def test_request_validation_identifies_field(client_app):
    @client_app.post("/_test/validated")
    async def _validated(payload: Payload) -> dict[str, str]:
        return {"name": payload.name}

    transport = httpx.ASGITransport(app=client_app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.post("/_test/validated", json={"name": "x", "count": "not-a-number"})

    body = response.json()
    assert response.status_code == 422
    assert body["error"]["details"][0]["field"] == "count"
