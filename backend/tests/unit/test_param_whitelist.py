"""参数白名单与 timeout 上界校验。"""

from __future__ import annotations

import pytest

from agent_platform.contexts.catalog.domain.params import (
    SUPPORTED_MODEL_PARAMS,
    normalize_params,
    validate_template,
)
from agent_platform.settings import get_settings
from agent_platform.shared.errors import ValidationFailedError


def test_supported_keys_match_adapter_whitelist() -> None:
    assert {"temperature", "top_p", "max_tokens", "max_completion_tokens"} <= (
        SUPPORTED_MODEL_PARAMS
    )
    assert "timeout" in SUPPORTED_MODEL_PARAMS
    assert "reasoning_effort" not in SUPPORTED_MODEL_PARAMS


def test_unsupported_key_rejected_with_name() -> None:
    with pytest.raises(ValidationFailedError) as excinfo:
        validate_template({"reasoning_effort": {"type": "string"}})
    error = excinfo.value
    assert error.details and error.details[0]["field"] == "param_template.reasoning_effort"
    assert "unsupported parameter" in error.details[0]["reason"]


def test_timeout_accepted_within_platform_limit() -> None:
    validate_template({"timeout": {"type": "number", "min": 1, "max": 30, "default": 12}})
    assert normalize_params({"timeout": {"type": "number"}}, {"timeout": 30}) == {"timeout": 30}


def test_timeout_default_over_limit_rejected_with_bound() -> None:
    limit = get_settings().sync_max_timeout_s
    with pytest.raises(ValidationFailedError) as excinfo:
        validate_template({"timeout": {"type": "number", "default": limit + 1}})
    detail = excinfo.value.details[0]
    assert detail["field"] == "params.timeout"
    assert f"must be <= {limit:g}" in detail["reason"]


def test_timeout_runtime_value_over_limit_rejected() -> None:
    limit = get_settings().sync_max_timeout_s
    with pytest.raises(ValidationFailedError):
        normalize_params({"timeout": {"type": "number"}}, {"timeout": limit + 1})


def test_timeout_must_be_positive() -> None:
    with pytest.raises(ValidationFailedError):
        validate_template({"timeout": {"type": "number", "min": 0}})
    with pytest.raises(ValidationFailedError):
        normalize_params({"timeout": {"type": "number"}}, {"timeout": 0})


def test_timeout_declared_max_over_limit_rejected() -> None:
    limit = get_settings().sync_max_timeout_s
    with pytest.raises(ValidationFailedError) as excinfo:
        validate_template({"timeout": {"type": "number", "max": limit + 10}})
    assert excinfo.value.details[0]["field"] == "param_template.timeout.max"
