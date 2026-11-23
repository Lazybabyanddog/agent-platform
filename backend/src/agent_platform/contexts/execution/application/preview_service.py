"""调试预览：走与保存版本相同的装配管线，执行结果不落版本、不改发布指针。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from agent_platform.contexts.agent.application.agent_service import agent_service
from agent_platform.contexts.agent.domain.enums import Environment
from agent_platform.contexts.agent.infrastructure.repositories import AgentRepository
from agent_platform.contexts.execution.application.invocation_service import invocation_service
from agent_platform.contexts.execution.application.session_service import (
    binding_unavailable,
    gate_agent,
    session_service,
)
from agent_platform.contexts.execution.domain.enums import RunKind, RunStatus
from agent_platform.shared.context import ActorContext
from agent_platform.shared.db import session_scope
from agent_platform.shared.errors import NotFoundError, ResourceUnavailableError


@dataclass(frozen=True, slots=True)
class PreviewResult:
    run_id: uuid.UUID
    trace_id: str
    status: RunStatus
    output: str | None
    usage: dict[str, int]
    error: dict[str, Any] | None
    config_hash: str
    preview_snapshot: dict[str, Any]


@dataclass(slots=True)
class PreviewService:
    async def preview(
        self,
        *,
        actor: ActorContext,
        agent_id: uuid.UUID,
        env: Environment,
        model_id: uuid.UUID,
        prompt_version_id: uuid.UUID,
        tool_version_ids: list[uuid.UUID],
        params: dict[str, Any],
        policy: dict[str, Any],
        values: dict[str, Any],
        preview_session_id: str | None = None,
        timeout_s: float | None = None,
    ) -> PreviewResult:
        async def assemble(session) -> tuple[dict[str, Any], str]:
            agent = await AgentRepository().by_id(
                session, tenant_id=actor.tenant_id, agent_id=agent_id
            )
            if agent is None:
                raise NotFoundError("agent not found")
            gate_agent(agent)
            assembled = await agent_service.assemble_snapshot(
                session,
                tenant_id=actor.tenant_id,
                agent_code=agent.code,
                model_id=model_id,
                prompt_version_id=prompt_version_id,
                tool_version_ids=tool_version_ids,
                params=params,
                policy=policy,
            )
            # 装配阶段已完成引用复检与入参/策略校验；不可用时直接 409，不产生 run。
            if not assembled.available:
                raise ResourceUnavailableError(
                    "referenced resources are not available", details=assembled.problems
                )
            return assembled.snapshot, assembled.config_hash

        binding = await session_service.resolve_preview(
            actor=actor,
            agent_id=agent_id,
            env=env,
            session_key=preview_session_id,
            assemble=assemble,
        )

        if binding is None:
            async with session_scope(actor.tenant_id) as session:
                snapshot, digest = await assemble(session)
            thread_id = None
            execution_session_id = None
        else:
            # 同一预览会话沿用首轮快照；引用失效时失败关闭而不是改用当前编辑态
            problems = await invocation_service._reference_problems(
                actor=actor, snapshot=binding.snapshot
            )
            if problems:
                raise binding_unavailable(problems)
            await session_service.acquire(actor=actor, binding=binding)
            snapshot = binding.snapshot
            digest = binding.config_hash or ""
            thread_id = binding.thread_id
            execution_session_id = binding.session_id

        try:
            result = await invocation_service.execute_snapshot(
                actor=actor,
                agent_id=agent_id,
                env=env,
                snapshot=snapshot,
                values=values,
                timeout_s=timeout_s,
                kind=RunKind.PREVIEW,
                config_hash=digest,
                preview_snapshot=snapshot,
                session_id=preview_session_id,
                execution_session_id=execution_session_id,
                thread_id=thread_id,
                skip_reference_check=True,
            )
        finally:
            if binding is not None:
                await session_service.release(actor=actor, binding=binding)

        return PreviewResult(
            run_id=result.run_id,
            trace_id=result.trace_id,
            status=result.status,
            output=result.output,
            usage=result.usage,
            error=result.error,
            config_hash=digest,
            preview_snapshot=snapshot,
        )


preview_service = PreviewService()
