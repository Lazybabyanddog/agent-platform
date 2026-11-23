"""Agent 生命周期状态机。

```
DRAFT --首次发布成功--> ACTIVE <--启用/停用--> INACTIVE
   |                      |                     |
   +----------------------+---------------------+--> ARCHIVED
```
"""

from __future__ import annotations

from agent_platform.contexts.agent.domain.enums import AgentStatus
from agent_platform.shared.errors import ConflictError

_TRANSITIONS: dict[AgentStatus, frozenset[AgentStatus]] = {
    AgentStatus.DRAFT: frozenset({AgentStatus.ACTIVE, AgentStatus.ARCHIVED}),
    AgentStatus.ACTIVE: frozenset({AgentStatus.INACTIVE, AgentStatus.ARCHIVED}),
    AgentStatus.INACTIVE: frozenset({AgentStatus.ACTIVE, AgentStatus.ARCHIVED}),
    AgentStatus.ARCHIVED: frozenset(),
}


def can_transition(current: AgentStatus, target: AgentStatus) -> bool:
    return target in _TRANSITIONS[current]


def ensure_transition(current: AgentStatus, target: AgentStatus) -> None:
    """非法转移统一以冲突错误拒绝，调用方无需各自重复判断。"""
    if can_transition(current, target):
        return
    raise ConflictError(
        f"cannot change agent status from {current.value} to {target.value}",
        code="AGENT_STATUS_TRANSITION",
        details=[
            {
                "field": "status",
                "reason": "transition not allowed",
                "current": current.value,
                "target": target.value,
            }
        ],
    )
