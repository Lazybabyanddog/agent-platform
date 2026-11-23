"""生效超时：模型级 timeout 只能收紧本次运行预算，不能放大。"""

from __future__ import annotations

from typing import Any

from agent_platform.contexts.execution.application.invocation_service import (
    _effective_client_timeout,
)


def _snapshot(params: dict[str, Any] | None = None) -> dict[str, Any]:
    model: dict[str, Any] = {}
    if params is not None:
        model["params"] = params
    return {"model": model}


def test_template_timeout_tightens_budget() -> None:
    assert _effective_client_timeout(_snapshot({"timeout": 5}), 30) == 5
    assert _effective_client_timeout(_snapshot({"timeout": 5.5}), 30) == 5.5


def test_budget_wins_when_tighter() -> None:
    assert _effective_client_timeout(_snapshot({"timeout": 60}), 30) == 30


def test_missing_or_invalid_timeout_falls_back_to_budget() -> None:
    assert _effective_client_timeout(_snapshot(), 30) == 30
    assert _effective_client_timeout(_snapshot({}), 30) == 30
    assert _effective_client_timeout(_snapshot({"timeout": 0}), 30) == 30
    assert _effective_client_timeout(_snapshot({"timeout": True}), 30) == 30
    assert _effective_client_timeout(_snapshot({"timeout": "5"}), 30) == 30
