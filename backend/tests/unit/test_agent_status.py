"""Agent 生命周期状态转移矩阵。"""

from __future__ import annotations

import pytest

from agent_platform.contexts.agent.domain.enums import AgentStatus
from agent_platform.contexts.agent.domain.status import can_transition, ensure_transition
from agent_platform.shared.errors import ConflictError

ALLOWED = {
    (AgentStatus.DRAFT, AgentStatus.ACTIVE),
    (AgentStatus.DRAFT, AgentStatus.ARCHIVED),
    (AgentStatus.ACTIVE, AgentStatus.INACTIVE),
    (AgentStatus.ACTIVE, AgentStatus.ARCHIVED),
    (AgentStatus.INACTIVE, AgentStatus.ACTIVE),
    (AgentStatus.INACTIVE, AgentStatus.ARCHIVED),
}


@pytest.mark.parametrize("current", list(AgentStatus))
@pytest.mark.parametrize("target", list(AgentStatus))
def test_transition_matrix(current: AgentStatus, target: AgentStatus) -> None:
    allowed = (current, target) in ALLOWED
    assert can_transition(current, target) is allowed
    if allowed:
        ensure_transition(current, target)
    else:
        with pytest.raises(ConflictError) as raised:
            ensure_transition(current, target)
        assert raised.value.code == "AGENT_STATUS_TRANSITION"
        assert raised.value.details[0]["current"] == current.value
        assert raised.value.details[0]["target"] == target.value


def test_archived_is_terminal() -> None:
    assert all(
        not can_transition(AgentStatus.ARCHIVED, target) for target in AgentStatus
    )
