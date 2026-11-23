"""会话保留期与历史上限的策略校验边界。"""

from __future__ import annotations

import pytest

from agent_platform.contexts.agent.domain.snapshot import normalize_policy
from agent_platform.shared.errors import ValidationFailedError

MAX_TIMEOUT_S = 120


@pytest.mark.parametrize("hours", [1, 168, 720])
def test_session_ttl_accepts_boundaries(hours: int) -> None:
    policy = normalize_policy({"session_ttl_h": hours}, max_timeout_s=MAX_TIMEOUT_S)
    assert policy["session_ttl_h"] == hours


@pytest.mark.parametrize("hours", [0, 721, "24", True])
def test_session_ttl_rejects_out_of_range(hours) -> None:
    with pytest.raises(ValidationFailedError) as caught:
        normalize_policy({"session_ttl_h": hours}, max_timeout_s=MAX_TIMEOUT_S)
    assert caught.value.details[0]["field"] == "policy.session_ttl_h"


@pytest.mark.parametrize("limit", [256, 4000, 32000])
def test_history_limit_accepts_boundaries(limit: int) -> None:
    policy = normalize_policy({"history_token_limit": limit}, max_timeout_s=MAX_TIMEOUT_S)
    assert policy["history_token_limit"] == limit


@pytest.mark.parametrize("limit", [255, 32001, 0, "4000"])
def test_history_limit_rejects_out_of_range(limit) -> None:
    with pytest.raises(ValidationFailedError) as caught:
        normalize_policy({"history_token_limit": limit}, max_timeout_s=MAX_TIMEOUT_S)
    assert caught.value.details[0]["field"] == "policy.history_token_limit"


def test_defaults_are_applied() -> None:
    policy = normalize_policy({}, max_timeout_s=MAX_TIMEOUT_S)
    assert policy["session_ttl_h"] == 168
    assert policy["history_token_limit"] == 4000
