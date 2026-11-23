"""响应 schema：region 为空/有值与最新探测摘要的结构。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

from agent_platform.contexts.catalog.api.schemas import ProbeSummary, ProviderResponse


def _provider(**overrides: object) -> SimpleNamespace:
    base = {
        "id": uuid.uuid4(),
        "name": "openai-main",
        "adapter": "openai_compatible",
        "base_url": "https://api.example.com/v1",
        "region": None,
        "secret_ref_id": None,
        "status": "ACTIVE",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_provider_response_without_region() -> None:
    response = ProviderResponse.model_validate(_provider())
    assert response.region is None
    assert response.latest_probe is None
    assert response.impact_notice is None


def test_provider_response_with_region_and_probe() -> None:
    response = ProviderResponse.model_validate(_provider(region="cn-north"))
    assert response.region == "cn-north"

    response.latest_probe = ProbeSummary(
        verdict="OK",
        created_at=datetime.now(UTC),
        observed_model_name="gpt-4o-mini",
    )
    payload = response.model_dump(mode="json")
    assert payload["latest_probe"]["verdict"] == "OK"
    assert payload["latest_probe"]["observed_model_name"] == "gpt-4o-mini"


def test_region_is_optional_in_payload() -> None:
    payload = _provider(region=None).__dict__
    payload.pop("region")
    assert ProviderResponse.model_validate(SimpleNamespace(**payload)).region is None
