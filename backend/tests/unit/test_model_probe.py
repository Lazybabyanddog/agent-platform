"""连通性探测六态判定与响应体脱敏。"""

from __future__ import annotations

import json
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from agent_platform.contexts.catalog.api.routes import _probe_response
from agent_platform.contexts.catalog.application import model_service as service_module
from agent_platform.contexts.catalog.application.model_service import (
    PROBE_AUTH_FAILED,
    PROBE_CREDENTIAL_OK_MODEL_UNVERIFIED,
    PROBE_MODEL_NOT_FOUND,
    PROBE_OBSERVED_MODELS_LIMIT,
    PROBE_OK,
    PROBE_TIMEOUT,
    PROBE_UNREACHABLE,
    ModelService,
)

SECRET = "sk-probe-secret"


def _service(handler: Any) -> ModelService:
    return ModelService(transport=httpx.MockTransport(handler))


async def _probe(handler: Any, model_name: str = "gpt-4o-mini") -> Any:
    service = _service(handler)
    return await service._probe_upstream(
        tenant_id=uuid.uuid4(),
        base_url="https://stub.local/v1",
        secret_ref_id=None,
        model_name=model_name,
    )


async def _model_listed(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"data": [{"id": "other"}, {"id": "gpt-4o-mini"}]})


async def _model_missing(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"data": [{"id": "other"}]})


async def _no_model_list(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"object": "list"})


async def _unauthorized(request: httpx.Request) -> httpx.Response:
    return httpx.Response(401, json={"error": "bad key"})


async def _timeout(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectTimeout("timed out")


async def _connection_error(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("unreachable")


async def test_probe_hit_returns_observed_name() -> None:
    result = await _probe(_model_listed)
    assert result.verdict == PROBE_OK
    assert result.ok is True
    assert result.observed_model_name == "gpt-4o-mini"
    assert result.model_count == 2
    assert result.observed_models == ["other", "gpt-4o-mini"]


async def test_probe_model_missing_from_list() -> None:
    result = await _probe(_model_missing)
    assert result.verdict == PROBE_MODEL_NOT_FOUND
    assert result.ok is False
    assert result.observed_models == ["other"]


async def test_probe_without_parsable_list() -> None:
    result = await _probe(_no_model_list)
    assert result.verdict == PROBE_CREDENTIAL_OK_MODEL_UNVERIFIED
    assert result.ok is False
    assert result.observed_models is None


async def test_probe_auth_failure() -> None:
    result = await _probe(_unauthorized)
    assert result.verdict == PROBE_AUTH_FAILED
    assert result.status_code == 401


async def test_probe_timeout() -> None:
    result = await _probe(_timeout)
    assert result.verdict == PROBE_TIMEOUT
    assert result.status_code is None


async def test_probe_unreachable() -> None:
    result = await _probe(_connection_error)
    assert result.verdict == PROBE_UNREACHABLE


async def test_observed_models_are_truncated() -> None:
    async def many_models(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"data": [{"id": f"m-{index}"} for index in range(80)]}
        )

    result = await _probe(many_models)
    assert result.observed_models is not None
    assert len(result.observed_models) == PROBE_OBSERVED_MODELS_LIMIT
    assert result.verdict == PROBE_MODEL_NOT_FOUND


async def test_provider_level_probe_passes_with_list() -> None:
    service = _service(_model_missing)
    provider_result = await service._probe_upstream(
        tenant_id=uuid.uuid4(),
        base_url="https://stub.local/v1",
        secret_ref_id=None,
        model_name=None,
    )
    assert provider_result.verdict == PROBE_OK
    assert provider_result.observed_model_name is None
    assert provider_result.model_count == 1


@pytest.mark.parametrize(
    "handler",
    [
        _model_listed,
        _model_missing,
        _no_model_list,
        _unauthorized,
        _timeout,
        _connection_error,
    ],
)
async def test_probe_response_never_contains_credentials(
    handler: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_resolve(*, tenant_id: uuid.UUID, secret_id: uuid.UUID) -> str:
        return SECRET

    monkeypatch.setattr(service_module.secret_service, "resolve", fake_resolve)
    service = _service(handler)
    result = await service._probe_upstream(
        tenant_id=uuid.uuid4(),
        base_url="https://stub.local/v1",
        secret_ref_id=uuid.uuid4(),
        model_name="gpt-4o-mini",
    )
    result = replace(result, probe_id=uuid.uuid4(), created_at=datetime.now(UTC))
    payload = _probe_response(result).model_dump(mode="json")
    assert SECRET not in json.dumps(payload)
    assert SECRET not in str(result)
