"""Agent 发布、回滚与发布指针。

发布 = 引用复检 + 新增发布记录 + 切换指针 + 审计 + Outbox 事件，全部在一个事务内；
回滚只切换指针，不改写任何历史发布记录。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from agent_platform.contexts.agent.domain.enums import AgentStatus, Environment, ReleaseStatus
from agent_platform.contexts.agent.domain.status import ensure_transition
from agent_platform.contexts.agent.infrastructure.repositories import (
    AgentRepository,
    AgentVersionRepository,
    PointerRepository,
    ReleaseRepository,
)
from agent_platform.contexts.agent.infrastructure.tables import AgentRelease, AgentVersion
from agent_platform.contexts.catalog.domain.enums import (
    ModelStatus,
    PromptStatus,
    ProviderStatus,
    ToolStatus,
)
from agent_platform.contexts.catalog.infrastructure.repositories import (
    ModelRepository,
    PromptRepository,
    PromptVersionRepository,
    ProviderRepository,
    ToolGrantRepository,
    ToolRepository,
    ToolVersionRepository,
)
from agent_platform.contexts.platform.application.audit_service import audit_service
from agent_platform.contexts.platform.domain.enums import AuditResult
from agent_platform.shared.context import ActorContext, get_trace_id
from agent_platform.shared.db import session_scope
from agent_platform.shared.errors import ConflictError, NotFoundError, ResourceUnavailableError
from agent_platform.shared.messaging.outbox import record_event

AGENT_RELEASE_PUBLISHED = "agent.release.published"


@dataclass(frozen=True, slots=True)
class PointerView:
    agent_id: uuid.UUID
    env: Environment
    release_id: uuid.UUID
    agent_version_id: uuid.UUID
    version_no: int
    updated_at: datetime


class AgentReleaseService:
    async def publish(
        self,
        *,
        actor: ActorContext,
        agent_id: uuid.UUID,
        agent_version_id: uuid.UUID,
        env: Environment,
        note: str | None = None,
    ) -> tuple[AgentRelease, AgentVersion]:
        async with session_scope(actor.tenant_id) as session:
            agent = await AgentRepository().by_id(
                session, tenant_id=actor.tenant_id, agent_id=agent_id
            )
            if agent is None:
                raise NotFoundError("agent not found")
            if agent.status == AgentStatus.ARCHIVED:
                raise ConflictError("agent is archived", code="AGENT_ARCHIVED")
            version = await AgentVersionRepository().by_id(
                session, tenant_id=actor.tenant_id, version_id=agent_version_id
            )
            if version is None or version.agent_id != agent_id:
                raise NotFoundError("agent version not found")

            problems = await self.reference_problems(
                session, tenant_id=actor.tenant_id, snapshot=version.snapshot
            )
            if problems:
                await audit_service.record_detached(
                    tenant_id=actor.tenant_id,
                    action=AGENT_RELEASE_PUBLISHED,
                    resource_type="agent",
                    resource_id=agent_id,
                    actor=actor,
                    result=AuditResult.FAILURE,
                    detail={
                        "env": env.value,
                        "agent_version_id": str(agent_version_id),
                        "problems": problems,
                    },
                )
                raise ResourceUnavailableError(
                    "referenced resources are not available", details=problems
                )

            release = await ReleaseRepository().create(
                session,
                tenant_id=actor.tenant_id,
                agent_id=agent_id,
                agent_version_id=agent_version_id,
                env=env,
                status=ReleaseStatus.RELEASED,
                released_by=actor.actor_id,
                released_at=datetime.now(UTC),
            )
            await PointerRepository().upsert(
                session,
                tenant_id=actor.tenant_id,
                agent_id=agent_id,
                env=env,
                release_id=release.id,
            )
            # 首次发布才进入 ACTIVE；停用期间发布不自动启用。
            if agent.status == AgentStatus.DRAFT:
                ensure_transition(agent.status, AgentStatus.ACTIVE)
                agent.status = AgentStatus.ACTIVE
            await audit_service.record(
                session,
                tenant_id=actor.tenant_id,
                action=AGENT_RELEASE_PUBLISHED,
                resource_type="agent",
                resource_id=agent_id,
                actor=actor,
                detail={
                    "env": env.value,
                    "release_id": str(release.id),
                    "agent_version_id": str(agent_version_id),
                    "version_no": version.version_no,
                    "note": note,
                    "status": agent.status.value,
                },
            )
            await record_event(
                session,
                tenant_id=actor.tenant_id,
                event_type=AGENT_RELEASE_PUBLISHED,
                payload={
                    "agent_id": str(agent_id),
                    "agent_version_id": str(agent_version_id),
                    "version_no": version.version_no,
                    "release_id": str(release.id),
                    "env": env.value,
                },
                trace_id=get_trace_id(),
            )
            return release, version

    async def rollback(
        self,
        *,
        actor: ActorContext,
        agent_id: uuid.UUID,
        env: Environment,
        release_id: uuid.UUID,
    ) -> tuple[AgentRelease, AgentVersion]:
        async with session_scope(actor.tenant_id) as session:
            agent = await AgentRepository().by_id(
                session, tenant_id=actor.tenant_id, agent_id=agent_id
            )
            if agent is None:
                raise NotFoundError("agent not found")
            if agent.status == AgentStatus.ARCHIVED:
                raise ConflictError("agent is archived", code="AGENT_ARCHIVED")
            release = await ReleaseRepository().by_id(
                session, tenant_id=actor.tenant_id, release_id=release_id
            )
            if release is None or release.agent_id != agent_id or release.env != env:
                raise NotFoundError("release not found for this agent and environment")
            version = await AgentVersionRepository().by_id(
                session, tenant_id=actor.tenant_id, version_id=release.agent_version_id
            )
            if version is None:
                raise NotFoundError("agent version not found")
            await PointerRepository().upsert(
                session,
                tenant_id=actor.tenant_id,
                agent_id=agent_id,
                env=env,
                release_id=release.id,
            )
            await audit_service.record(
                session,
                tenant_id=actor.tenant_id,
                action="agent.release.rolled_back",
                resource_type="agent",
                resource_id=agent_id,
                actor=actor,
                detail={
                    "env": env.value,
                    "release_id": str(release.id),
                    "agent_version_id": str(release.agent_version_id),
                },
            )
            return release, version

    async def list_releases(
        self,
        *,
        actor: ActorContext,
        agent_id: uuid.UUID,
        env: Environment | None = None,
    ) -> list[tuple[AgentRelease, AgentVersion]]:
        async with session_scope(actor.tenant_id) as session:
            if (
                await AgentRepository().by_id(
                    session, tenant_id=actor.tenant_id, agent_id=agent_id
                )
                is None
            ):
                raise NotFoundError("agent not found")
            return await ReleaseRepository().list_with_version(
                session, tenant_id=actor.tenant_id, agent_id=agent_id, env=env
            )

    async def get_pointer(
        self, *, actor: ActorContext, agent_id: uuid.UUID, env: Environment
    ) -> PointerView:
        async with session_scope(actor.tenant_id) as session:
            if (
                await AgentRepository().by_id(
                    session, tenant_id=actor.tenant_id, agent_id=agent_id
                )
                is None
            ):
                raise NotFoundError("agent not found")
            found = await PointerRepository().get_with_release(
                session, tenant_id=actor.tenant_id, agent_id=agent_id, env=env
            )
            if found is None:
                raise NotFoundError("release pointer not found")
            pointer, release, version = found
            return PointerView(
                agent_id=agent_id,
                env=env,
                release_id=release.id,
                agent_version_id=release.agent_version_id,
                version_no=version.version_no,
                updated_at=pointer.updated_at,
            )

    # ------------------------------------------------------------------

    async def reference_problems(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        snapshot: dict[str, Any],
        check_model_status: bool = True,
    ) -> list[dict[str, Any]]:
        """复检快照引用的资源与授权是否仍然可用。

        ``check_model_status=False`` 供调用路径使用：已发布版本按快照继续运行，
        模型或供应商停用不阻断调用（D7）；资源本身缺失仍然阻断。
        """
        problems: list[dict[str, Any]] = []
        model_snapshot = snapshot.get("model") or {}
        model_id = model_snapshot.get("model_id")
        model = (
            await ModelRepository().by_id(
                session, tenant_id=tenant_id, model_id=uuid.UUID(str(model_id))
            )
            if model_id
            else None
        )
        if model is None:
            problems.append(
                {"field": "model_id", "resource_id": str(model_id), "reason": "not found"}
            )
        elif check_model_status and model.status != ModelStatus.ACTIVE:
            problems.append(
                {
                    "field": "model_id",
                    "resource_id": str(model_id),
                    "reason": "model is disabled",
                }
            )
        else:
            # 与生成版本时的校验一致：供应商停用会阻断发布，但已发布版本继续按原配置运行
            provider = await ProviderRepository().by_id(
                session, tenant_id=tenant_id, provider_id=model.provider_id
            )
            if provider is None:
                problems.append(
                    {"field": "model_id", "resource_id": str(model_id), "reason": "provider not found"}
                )
            elif check_model_status and provider.status != ProviderStatus.ACTIVE:
                problems.append(
                    {
                        "field": "model_id",
                        "resource_id": str(model_id),
                        "reason": f"provider {provider.name} is disabled",
                    }
                )

        prompt_snapshot = snapshot.get("prompt") or {}
        prompt_version_id = prompt_snapshot.get("prompt_version_id")
        prompt_version = (
            await PromptVersionRepository().by_id(
                session, tenant_id=tenant_id, version_id=uuid.UUID(str(prompt_version_id))
            )
            if prompt_version_id
            else None
        )
        if prompt_version is None:
            problems.append(
                {
                    "field": "prompt_version_id",
                    "resource_id": str(prompt_version_id),
                    "reason": "not found",
                }
            )
        else:
            prompt = await PromptRepository().by_id(
                session, tenant_id=tenant_id, prompt_id=prompt_version.prompt_id
            )
            if prompt is None or prompt.status != PromptStatus.ACTIVE:
                problems.append(
                    {
                        "field": "prompt_version_id",
                        "resource_id": str(prompt_version_id),
                        "reason": "prompt is archived",
                    }
                )

        for tool in snapshot.get("tools", []):
            tool_version_id = tool.get("tool_version_id")
            tool_version = (
                await ToolVersionRepository().by_id(
                    session, tenant_id=tenant_id, version_id=uuid.UUID(str(tool_version_id))
                )
                if tool_version_id
                else None
            )
            if tool_version is None:
                problems.append(
                    {
                        "field": "tool_version_ids",
                        "resource_id": str(tool_version_id),
                        "reason": "not found",
                    }
                )
                continue
            resolved = await ToolRepository().by_id(
                session, tenant_id=tenant_id, tool_id=tool_version.tool_id
            )
            if resolved is None or resolved.status != ToolStatus.ACTIVE:
                problems.append(
                    {
                        "field": "tool_version_ids",
                        "resource_id": str(tool_version_id),
                        "reason": "tool is disabled",
                    }
                )
                continue
            if (
                await ToolGrantRepository().get(
                    session, tenant_id=tenant_id, tool_id=resolved.id
                )
                is None
            ):
                problems.append(
                    {
                        "field": "tool_version_ids",
                        "resource_id": str(tool_version_id),
                        "reason": "tool is not granted",
                    }
                )
        return problems


agent_release_service = AgentReleaseService()
