"""执行预算：步骤、token 与总超时。"""

from __future__ import annotations

from dataclasses import dataclass

from agent_platform.contexts.execution.domain.enums import RunErrorCode


class BudgetExceededError(Exception):
    """预算耗尽时中止执行，由运行记录映射为 BUDGET_EXCEEDED。"""

    def __init__(self, limit: str) -> None:
        super().__init__(f"{limit} budget exceeded")
        self.code = RunErrorCode.BUDGET_EXCEEDED
        self.limit = limit


@dataclass(slots=True)
class ExecutionBudget:
    max_steps: int
    token_budget: int
    timeout_s: float
    used_steps: int = 0
    used_tokens: int = 0

    def consume_step(self) -> None:
        if self.used_steps >= self.max_steps:
            raise BudgetExceededError("STEP_LIMIT")
        self.used_steps += 1

    def consume_tokens(self, tokens: int) -> None:
        self.used_tokens += max(tokens, 0)
        if self.used_tokens > self.token_budget:
            raise BudgetExceededError("TOKEN_BUDGET")
