"""params.timeout 的接线：收紧客户端超时、不泄漏到适配器 kwargs。"""

from __future__ import annotations

from typing import Any

import pytest

from agent_platform.contexts.catalog.domain.params import SAMPLING_PARAM_KEYS
from agent_platform.contexts.execution.application.invocation_service import (
    _effective_client_timeout,
)
from agent_platform.contexts.execution.infrastructure.llm import langchain_adapter


def _snapshot(params: dict[str, Any] | None) -> dict[str, Any]:
    return {"model": {"model_id": "m", "model_name": "gpt-4o-mini", "params": params}}


def test_timeout_param_tightens_client_timeout() -> None:
    assert _effective_client_timeout(_snapshot({"timeout": 5}), 30) == 5


def test_timeout_param_never_exceeds_run_budget() -> None:
    assert _effective_client_timeout(_snapshot({"timeout": 120}), 30) == 30


@pytest.mark.parametrize("params", [None, {}, {"timeout": 0}, {"timeout": "8"}])
def test_without_valid_timeout_param_budget_is_used(params: dict[str, Any] | None) -> None:
    assert _effective_client_timeout(_snapshot(params), 30) == 30


def test_adapter_whitelist_matches_supported_sampling_params() -> None:
    assert langchain_adapter._ALLOWED_PARAMS == set(SAMPLING_PARAM_KEYS)
    assert "timeout" not in langchain_adapter._ALLOWED_PARAMS


def test_adapter_drops_timeout_from_langchain_kwargs(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_chat_openai(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(langchain_adapter, "ChatOpenAI", fake_chat_openai)
    adapter = langchain_adapter.LangChainModelAdapter(
        base_url="https://upstream.example/v1", api_key="k", timeout_s=30
    )
    adapter._client("gpt-4o-mini", {"timeout": 5, "temperature": 0.2})

    assert captured["timeout"] == 30
    assert captured["temperature"] == 0.2
