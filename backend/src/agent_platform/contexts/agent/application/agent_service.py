"""Agent 与版本快照。

建版本 = 解析引用 + 冻结内容 + 记录绑定 + 计算 hash，全部在同一个事务里完成，
所以拿到版本号时就一定拿到了一份自洽、可回放的配置。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.exc import IntegrityError

from agent_platform.contexts.agent.domain.enums import AgentStatus, AgentType, Environment
from agent_platform.contexts.agent.domain.snapshot import (
    SNAPSHOT_SCHEMA_VERSION,
    bindings_for_snapshot,
    build_snapshot,
    diff_snapshots,
    normalize_policy,
)
from agent_platform.contexts.agent.domain.status import ensure_transition
from agent_platform.contexts.agent.infrastructure.repositories import (
    AgentRepository,
    AgentVersionRepository,
    BindingRepository,
    PointerRepository,
    ReleaseRepository,
)
from agent_platform.contexts.agent.infrastructure.tables import Agent, AgentVersion
from agent_platform.contexts.catalog.domain.enums import (
    ModelStatus,
    PromptStatus,
    ProviderStatus,
    ToolStatus,
)
from agent_platform.contexts.catalog.domain.params import normalize_params
from agent_platform.contexts.catalog.infrastructure.repositories import (
    ModelRepository,
    PromptRepository,
    PromptVersionRepository,
    ProviderRepository,
    ToolGrantRepository,
    ToolRepository,
    ToolVersionRepository,
)
from agent_platform.contexts.execution.infrastructure.repositories import RunRepository
from agent_platform.contexts.platform.application.audit_service import audit_service
from agent_platform.settings import get_settings
from agent_platform.shared.context import ActorContext
from agent_platform.shared.db import session_scope
from agent_platform.shared.errors import (
    ConflictError,
    NotFoundError,
    ResourceUnavailableError,
    ValidationFailedError,
)
from agent_platform.shared.hashing import config_hash


@dataclass(frozen=True, slots=True)
class AgentListItem:
    """列表/详情条目：身份与三环境生效状态一起返回。"""

    agent: Agent
    environments: dict[str, dict[str, Any] | None]


@dataclass(frozen=True, slots=True)
class AgentReadiness:
    agent_id: uuid.UUID
    agent_version_id: uuid.UUID
    version_no: int
    config_hash: str
    ready: bool
    bindings: list[dict[str, Any]]
    preview: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class AssembleResult:
    """装配结果：快照与不可用引用明细。

    保存版本与预览执行都只拿这一份结果，前者在无问题时才落库，后者直接执行，
    因此"预览能跑、保存报错"在结构上不会出现。
    """

    snapshot: dict[str, Any]
    config_hash: str
    problems: list[dict[str, Any]] = field(default_factory=list)

    @property
    def available(self) -> bool:
        return not self.problems


class AgentService:
    async def create(
        self,
        *,
        actor: ActorContext,
        code: str,
        name: str,
        description: str | None = None,
        tags: list[str] | None = None,
        agent_type: AgentType = AgentType.CHAT,
        model_id: uuid.UUID | None = None,
        prompt_version_id: uuid.UUID | None = None,
        tool_version_ids: list[uuid.UUID] | None = None,
        params: dict[str, Any] | None = None,
        policy: dict[str, Any] | None = None,
    ) -> Agent:
        """创建逻辑身份；提供初始配置时在同一事务内生成版本 1。

        装配失败会让整个事务回滚，租户下不会留下"有身份没配置"的半成品。
        """
        has_config = any(item is not None for item in (model_id, prompt_version_id))
        if has_config and (model_id is None or prompt_version_id is None):
            raise ValidationFailedError(
                "initial config requires both model_id and prompt_version_id",
                details=[{"field": "config", "reason": "model_id and prompt_version_id are required"}],
            )

        async with session_scope(actor.tenant_id) as session:
            repo = AgentRepository()
            if await repo.by_code(session, tenant_id=actor.tenant_id, code=code):
                raise ConflictError(f"agent code {code!r} already exists")
            agent = await repo.create(
                session,
                tenant_id=actor.tenant_id,
                code=code,
                name=name,
                description=description,
                type=agent_type,
                owner_id=actor.actor_id,
                tags=tags or [],
                status=AgentStatus.DRAFT,
            )
            if model_id is not None and prompt_version_id is not None:
                assembled = await self.assemble_snapshot(
                    session,
                    tenant_id=actor.tenant_id,
                    agent_code=agent.code,
                    model_id=model_id,
                    prompt_version_id=prompt_version_id,
                    tool_version_ids=tool_version_ids or [],
                    params=params,
                    policy=policy,
                )
                if not assembled.available:
                    raise ResourceUnavailableError(
                        "referenced resources are not available", details=assembled.problems
                    )
                await self._persist_version(
                    session,
                    tenant_id=actor.tenant_id,
                    agent_id=agent.id,
                    assembled=assembled,
                    note="initial config",
                    created_by=actor.actor_id,
                )
            return agent

    async def copy(
        self,
        *,
        actor: ActorContext,
        agent_id: uuid.UUID,
        code: str,
        name: str | None = None,
    ) -> Agent:
        """复制身份与最新配置；不复制发布、指针与运行记录。"""
        async with session_scope(actor.tenant_id) as session:
            repo = AgentRepository()
            source = await repo.by_id(session, tenant_id=actor.tenant_id, agent_id=agent_id)
            if source is None:
                raise NotFoundError("agent not found")
            if source.status == AgentStatus.ARCHIVED:
                raise ConflictError("agent is archived", code="AGENT_ARCHIVED")
            if await repo.by_code(session, tenant_id=actor.tenant_id, code=code):
                raise ConflictError(f"agent code {code!r} already exists")

            copied = await repo.create(
                session,
                tenant_id=actor.tenant_id,
                code=code,
                name=name or f"{source.name} (copy)",
                description=source.description,
                type=source.type,
                owner_id=actor.actor_id,
                tags=list(source.tags),
                status=AgentStatus.DRAFT,
            )
            latest = await AgentVersionRepository().latest_for_agent(
                session, agent_id=source.id
            )
            if latest is not None:
                version = await AgentVersionRepository().create(
                    session,
                    tenant_id=actor.tenant_id,
                    agent_id=copied.id,
                    version_no=1,
                    snapshot_schema_version=latest.snapshot_schema_version,
                    snapshot=latest.snapshot,
                    config_hash=latest.config_hash,
                    note=f"copied from {source.code}#v{latest.version_no}",
                    created_by=actor.actor_id,
                )
                await BindingRepository().create_many(
                    session,
                    tenant_id=actor.tenant_id,
                    bindings=bindings_for_snapshot(
                        agent_version_id=version.id, snapshot=version.snapshot
                    ),
                )
            await audit_service.record(
                session,
                tenant_id=actor.tenant_id,
                action="agent.copied",
                resource_type="agent",
                resource_id=copied.id,
                actor=actor,
                detail={
                    "source_agent_id": str(source.id),
                    "source_code": source.code,
                    "source_version_no": latest.version_no if latest else None,
                    "config_hash": latest.config_hash if latest else None,
                },
            )
            return copied

    async def activate(self, *, actor: ActorContext, agent_id: uuid.UUID) -> Agent:
        """重新启用：只对发布过的 Agent 有效，且不改动发布指针。"""
        async with session_scope(actor.tenant_id) as session:
            agent = await self._load(session, tenant_id=actor.tenant_id, agent_id=agent_id)
            if agent.status == AgentStatus.ACTIVE:
                return agent
            ensure_transition(agent.status, AgentStatus.ACTIVE)
            if (
                await ReleaseRepository().count_for_agent(
                    session, tenant_id=actor.tenant_id, agent_id=agent_id
                )
                == 0
            ):
                raise ConflictError(
                    "agent has never been published",
                    code="AGENT_NOT_PUBLISHED",
                    details=[
                        {
                            "field": "status",
                            "reason": "publish the agent at least once before activating it",
                        }
                    ],
                )
            previous = agent.status
            agent.status = AgentStatus.ACTIVE
            await audit_service.record(
                session,
                tenant_id=actor.tenant_id,
                action="agent.activated",
                resource_type="agent",
                resource_id=agent.id,
                actor=actor,
                detail={"from": previous.value, "to": AgentStatus.ACTIVE.value},
            )
            await session.refresh(agent)
            return agent

    async def deactivate(self, *, actor: ActorContext, agent_id: uuid.UUID) -> Agent:
        """停用：立即阻断新调用，发布记录与指针保持不变。"""
        async with session_scope(actor.tenant_id) as session:
            agent = await self._load(session, tenant_id=actor.tenant_id, agent_id=agent_id)
            if agent.status == AgentStatus.INACTIVE:
                return agent
            ensure_transition(agent.status, AgentStatus.INACTIVE)
            agent.status = AgentStatus.INACTIVE
            await audit_service.record(
                session,
                tenant_id=actor.tenant_id,
                action="agent.deactivated",
                resource_type="agent",
                resource_id=agent.id,
                actor=actor,
                detail={"to": AgentStatus.INACTIVE.value},
            )
            await session.refresh(agent)
            return agent

    async def archive(self, *, actor: ActorContext, agent_id: uuid.UUID) -> Agent:
        """归档：保留编码与全部历史，终态不可回退。"""
        async with session_scope(actor.tenant_id) as session:
            agent = await self._load(session, tenant_id=actor.tenant_id, agent_id=agent_id)
            if agent.status == AgentStatus.ARCHIVED:
                return agent
            ensure_transition(agent.status, AgentStatus.ARCHIVED)
            previous = agent.status
            agent.status = AgentStatus.ARCHIVED
            await audit_service.record(
                session,
                tenant_id=actor.tenant_id,
                action="agent.archived",
                resource_type="agent",
                resource_id=agent.id,
                actor=actor,
                detail={"from": previous.value, "to": AgentStatus.ARCHIVED.value},
            )
            await session.refresh(agent)
            return agent

    async def delete(self, *, actor: ActorContext, agent_id: uuid.UUID) -> None:
        """删除仅限从未产生版本、发布与运行记录的空壳，编码随之释放。"""
        async with session_scope(actor.tenant_id) as session:
            agent = await self._load(session, tenant_id=actor.tenant_id, agent_id=agent_id)
            counts = {
                "versions": await AgentVersionRepository().count_for_agent(
                    session, agent_id=agent_id
                ),
                "releases": await ReleaseRepository().count_for_agent(
                    session, tenant_id=actor.tenant_id, agent_id=agent_id
                ),
                "runs": await RunRepository().count_for_agent(
                    session, tenant_id=actor.tenant_id, agent_id=agent_id
                ),
            }
            if any(counts.values()):
                raise ConflictError(
                    "agent has history and cannot be deleted; archive it instead",
                    code="AGENT_HAS_HISTORY",
                    details=[
                        {
                            "field": "agent_id",
                            "reason": "archive instead of delete",
                            **{f"{key}_count": value for key, value in counts.items()},
                        }
                    ],
                )
            await audit_service.record(
                session,
                tenant_id=actor.tenant_id,
                action="agent.deleted",
                resource_type="agent",
                resource_id=agent.id,
                actor=actor,
                detail={"code": agent.code, "status": agent.status.value},
            )
            await session.delete(agent)

    async def list(self, *, actor: ActorContext) -> list[Agent]:
        async with session_scope(actor.tenant_id) as session:
            return await AgentRepository().list(session, tenant_id=actor.tenant_id)

    async def list_page(
        self,
        *,
        actor: ActorContext,
        status: AgentStatus | None = None,
        agent_type: AgentType | None = None,
        tag: str | None = None,
        q: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[AgentListItem], int]:
        """一页 Agent 加三环境生效状态，固定两次查询。"""
        async with session_scope(actor.tenant_id) as session:
            repo = AgentRepository()
            offset = (page - 1) * page_size
            agents = await repo.list_page(
                session,
                tenant_id=actor.tenant_id,
                status=status,
                agent_type=agent_type,
                tag=tag,
                q=q,
                limit=page_size,
                offset=offset,
            )
            total = await repo.count_page(
                session,
                tenant_id=actor.tenant_id,
                status=status,
                agent_type=agent_type,
                tag=tag,
                q=q,
            )
            environments = await self._environments_map(
                session, tenant_id=actor.tenant_id, agent_ids=[item.id for item in agents]
            )
            return (
                [
                    AgentListItem(agent=item, environments=environments[item.id])
                    for item in agents
                ],
                total,
            )

    async def get(self, *, actor: ActorContext, agent_id: uuid.UUID) -> Agent:
        async with session_scope(actor.tenant_id) as session:
            return await self._load(session, tenant_id=actor.tenant_id, agent_id=agent_id)

    async def get_detail(self, *, actor: ActorContext, agent_id: uuid.UUID) -> AgentListItem:
        async with session_scope(actor.tenant_id) as session:
            agent = await self._load(session, tenant_id=actor.tenant_id, agent_id=agent_id)
            environments = await self._environments_map(
                session, tenant_id=actor.tenant_id, agent_ids=[agent_id]
            )
            return AgentListItem(agent=agent, environments=environments[agent_id])

    async def _environments_map(
        self,
        session,
        *,
        tenant_id: uuid.UUID,
        agent_ids: list[uuid.UUID],
    ) -> dict[uuid.UUID, dict[str, dict[str, Any] | None]]:
        result: dict[uuid.UUID, dict[str, dict[str, Any] | None]] = {
            agent_id: {env.value: None for env in Environment} for agent_id in agent_ids
        }
        rows = await PointerRepository().list_with_release(
            session, tenant_id=tenant_id, agent_ids=agent_ids
        )
        for pointer, release, version in rows:
            result[pointer.agent_id][pointer.env.value] = {
                "agent_version_id": version.id,
                "version_no": version.version_no,
                "release_id": release.id,
                "released_at": release.released_at,
            }
        return result

    async def readiness(
        self, *, actor: ActorContext, agent_id: uuid.UUID, version_id: uuid.UUID
    ) -> AgentReadiness:
        """发布前体检：逐个绑定给可用性与原因，并附同摘要的最近预览。"""
        async with session_scope(actor.tenant_id) as session:
            await self._load(session, tenant_id=actor.tenant_id, agent_id=agent_id)
            version = await AgentVersionRepository().by_id(
                session, tenant_id=actor.tenant_id, version_id=version_id
            )
            if version is None or version.agent_id != agent_id:
                raise NotFoundError("agent version not found")

            snapshot = version.snapshot
            _, model_problems = await self._resolve_model(
                session,
                tenant_id=actor.tenant_id,
                model_id=uuid.UUID(str(snapshot["model"]["model_id"])),
                params=snapshot["model"].get("params"),
            )
            _, prompt_problems = await self._resolve_prompt(
                session,
                tenant_id=actor.tenant_id,
                prompt_version_id=uuid.UUID(str(snapshot["prompt"]["prompt_version_id"])),
            )
            _, tool_problems = await self._resolve_tools(
                session,
                tenant_id=actor.tenant_id,
                tool_version_ids=[
                    uuid.UUID(str(item["tool_version_id"]))
                    for item in snapshot.get("tools", [])
                ],
            )
            tool_reasons = {
                problem["resource_id"]: problem["reason"] for problem in tool_problems
            }

            bindings: list[dict[str, Any]] = [
                {
                    "role": "MODEL",
                    "resource_type": "MODEL",
                    "resource_id": snapshot["model"]["model_id"],
                    "resource_version_id": None,
                    "available": not model_problems,
                    "reason": model_problems[0]["reason"] if model_problems else None,
                },
                {
                    "role": "PROMPT",
                    "resource_type": "PROMPT_VERSION",
                    "resource_id": snapshot["prompt"]["prompt_id"],
                    "resource_version_id": snapshot["prompt"]["prompt_version_id"],
                    "available": not prompt_problems,
                    "reason": prompt_problems[0]["reason"] if prompt_problems else None,
                },
            ]
            for tool in snapshot.get("tools", []):
                reason = tool_reasons.get(str(tool["tool_version_id"]))
                bindings.append(
                    {
                        "role": "TOOL",
                        "resource_type": "TOOL_VERSION",
                        "resource_id": tool["tool_id"],
                        "resource_version_id": tool["tool_version_id"],
                        "available": reason is None,
                        "reason": reason,
                    }
                )

            run = await RunRepository().latest_preview_for_config(
                session,
                tenant_id=actor.tenant_id,
                agent_id=agent_id,
                config_hash=version.config_hash,
            )
            preview = (
                {
                    "run_id": run.id,
                    "status": run.status.value if hasattr(run.status, "value") else run.status,
                    "started_at": run.started_at,
                    "finished_at": run.finished_at,
                    "trace_id": run.trace_id,
                }
                if run is not None
                else None
            )
            return AgentReadiness(
                agent_id=agent_id,
                agent_version_id=version.id,
                version_no=version.version_no,
                config_hash=version.config_hash,
                ready=all(item["available"] for item in bindings),
                bindings=bindings,
                preview=preview,
            )

    async def list_versions(self, *, actor: ActorContext, agent_id: uuid.UUID) -> list[AgentVersion]:
        async with session_scope(actor.tenant_id) as session:
            if await AgentRepository().by_id(
                session, tenant_id=actor.tenant_id, agent_id=agent_id
            ) is None:
                raise NotFoundError("agent not found")
            return await AgentVersionRepository().list_for_agent(session, agent_id=agent_id)

    async def get_version(self, *, actor: ActorContext, version_id: uuid.UUID) -> AgentVersion:
        async with session_scope(actor.tenant_id) as session:
            version = await AgentVersionRepository().by_id(
                session, tenant_id=actor.tenant_id, version_id=version_id
            )
            if version is None:
                raise NotFoundError("agent version not found")
            return version

    async def diff_versions(
        self,
        *,
        actor: ActorContext,
        base_version_id: uuid.UUID,
        target_version_id: uuid.UUID,
    ) -> dict[str, Any]:
        """对比两个版本的资源绑定与运行策略，用于发布前确认变更范围。"""
        async with session_scope(actor.tenant_id) as session:
            repo = AgentVersionRepository()
            base = await repo.by_id(session, tenant_id=actor.tenant_id, version_id=base_version_id)
            target = await repo.by_id(
                session, tenant_id=actor.tenant_id, version_id=target_version_id
            )
            if base is None or target is None:
                raise NotFoundError("agent version not found")
            if base.agent_id != target.agent_id:
                raise ValidationFailedError(
                    "versions belong to different agents",
                    details=[
                        {
                            "field": "target_version_id",
                            "reason": "must belong to the same agent as base_version_id",
                        }
                    ],
                )
            return {
                "base_version_no": base.version_no,
                "target_version_no": target.version_no,
                "config_hash_changed": base.config_hash != target.config_hash,
                **diff_snapshots(base.snapshot, target.snapshot),
            }

    async def create_version(
        self,
        *,
        actor: ActorContext,
        agent_id: uuid.UUID,
        model_id: uuid.UUID,
        prompt_version_id: uuid.UUID,
        tool_version_ids: list[uuid.UUID] | None = None,
        params: dict[str, Any] | None = None,
        policy: dict[str, Any] | None = None,
        note: str | None = None,
        base_version_id: uuid.UUID | None = None,
    ) -> AgentVersion:
        async with session_scope(actor.tenant_id) as session:
            agent = await AgentRepository().by_id(
                session, tenant_id=actor.tenant_id, agent_id=agent_id
            )
            if agent is None:
                raise NotFoundError("agent not found")
            if agent.status == AgentStatus.ARCHIVED:
                raise ConflictError("agent is archived", code="AGENT_ARCHIVED")

            assembled = await self.assemble_snapshot(
                session,
                tenant_id=actor.tenant_id,
                agent_code=agent.code,
                model_id=model_id,
                prompt_version_id=prompt_version_id,
                tool_version_ids=tool_version_ids or [],
                params=params,
                policy=policy,
            )
            if not assembled.available:
                raise ResourceUnavailableError(
                    "referenced resources are not available", details=assembled.problems
                )

            latest = await AgentVersionRepository().latest_for_agent(
                session, agent_id=agent_id
            )
            if base_version_id is not None:
                base = await AgentVersionRepository().by_id(
                    session, tenant_id=actor.tenant_id, version_id=base_version_id
                )
                if base is None or base.agent_id != agent_id:
                    raise ValidationFailedError.for_field(
                        "base_version_id", "not a version of this agent"
                    )
                if latest is None or latest.id != base.id:
                    details: dict[str, Any] = {
                        "field": "base_version_id",
                        "reason": "not the latest version",
                        "base_version_no": base.version_no,
                        "latest_version_no": latest.version_no if latest else None,
                        "latest_version_id": str(latest.id) if latest else None,
                    }
                    if latest is not None:
                        details["diff"] = diff_snapshots(base.snapshot, latest.snapshot)
                    raise ConflictError(
                        "agent has a newer version",
                        code="AGENT_VERSION_CONFLICT",
                        details=[details],
                    )

            return await self._persist_version(
                session,
                tenant_id=actor.tenant_id,
                agent_id=agent_id,
                assembled=assembled,
                note=note,
                created_by=actor.actor_id,
            )

    async def assemble_snapshot(
        self,
        session,
        *,
        tenant_id: uuid.UUID,
        agent_code: str,
        model_id: uuid.UUID,
        prompt_version_id: uuid.UUID,
        tool_version_ids: list[uuid.UUID] | None = None,
        params: dict[str, Any] | None = None,
        policy: dict[str, Any] | None = None,
    ) -> AssembleResult:
        """解析引用并组装快照，不落库。

        保存版本与预览执行共用这一条管线：都走同一套可用性校验、参数归一化、
        策略校验与摘要计算，差异只在于调用方是否持久化。
        """
        settings = get_settings()
        normalized_policy = normalize_policy(policy, max_timeout_s=settings.sync_max_timeout_s)

        model_snapshot, problems = await self._resolve_model(
            session, tenant_id=tenant_id, model_id=model_id, params=params
        )
        prompt_snapshot, prompt_problems = await self._resolve_prompt(
            session, tenant_id=tenant_id, prompt_version_id=prompt_version_id
        )
        tool_snapshot, tool_problems = await self._resolve_tools(
            session, tenant_id=tenant_id, tool_version_ids=tool_version_ids or []
        )
        problems = [*problems, *prompt_problems, *tool_problems]

        snapshot = build_snapshot(
            agent_code=agent_code,
            model=model_snapshot,
            prompt=prompt_snapshot,
            tools=tool_snapshot,
            policy=normalized_policy,
        )
        return AssembleResult(
            snapshot=snapshot,
            config_hash=config_hash(snapshot),
            problems=problems,
        )

    async def _persist_version(
        self,
        session,
        *,
        tenant_id: uuid.UUID,
        agent_id: uuid.UUID,
        assembled: AssembleResult,
        note: str | None,
        created_by: uuid.UUID | None,
    ) -> AgentVersion:
        repo = AgentVersionRepository()
        version_no = await repo.next_version_no(session, agent_id=agent_id)
        try:
            async with session.begin_nested():
                version = await repo.create(
                    session,
                    tenant_id=tenant_id,
                    agent_id=agent_id,
                    version_no=version_no,
                    snapshot_schema_version=SNAPSHOT_SCHEMA_VERSION,
                    snapshot=assembled.snapshot,
                    config_hash=assembled.config_hash,
                    note=note,
                    created_by=created_by,
                )
        except IntegrityError as exc:
            raise ConflictError("agent version conflict, retry the request") from exc

        await BindingRepository().create_many(
            session,
            tenant_id=tenant_id,
            bindings=bindings_for_snapshot(
                agent_version_id=version.id, snapshot=version.snapshot
            ),
        )
        return version

    # ------------------------------------------------------------------

    async def _load(self, session, *, tenant_id: uuid.UUID, agent_id: uuid.UUID) -> Agent:
        agent = await AgentRepository().by_id(
            session, tenant_id=tenant_id, agent_id=agent_id
        )
        if agent is None:
            raise NotFoundError("agent not found")
        return agent

    async def _resolve_model(
        self,
        session,
        *,
        tenant_id: uuid.UUID,
        model_id: uuid.UUID,
        params: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        model = await ModelRepository().by_id(session, tenant_id=tenant_id, model_id=model_id)
        if model is None:
            return {}, [{"field": "model_id", "resource_id": str(model_id), "reason": "not found"}]
        if model.status != ModelStatus.ACTIVE:
            return (
                {},
                [{"field": "model_id", "resource_id": str(model_id), "reason": "model is disabled"}],
            )
        # 供应商停用只拒绝新绑定与发布；已发布版本走执行层，不受此校验影响
        provider = await ProviderRepository().by_id(
            session, tenant_id=tenant_id, provider_id=model.provider_id
        )
        if provider is None:
            return (
                {},
                [
                    {
                        "field": "model_id",
                        "resource_id": str(model_id),
                        "reason": "model provider not found",
                    }
                ],
            )
        if provider.status != ProviderStatus.ACTIVE:
            return (
                {},
                [
                    {
                        "field": "model_id",
                        "resource_id": str(model_id),
                        "reason": f"model provider {provider.name} is disabled",
                    }
                ],
            )
        normalized = normalize_params(model.param_template, params)
        return (
            {
                "model_id": str(model.id),
                "code": model.code,
                "model_name": model.model_name,
                "ctx_window": model.ctx_window,
                "capabilities": model.capabilities,
                "param_template": model.param_template,
                "params": normalized,
            },
            [],
        )

    async def _resolve_prompt(
        self, session, *, tenant_id: uuid.UUID, prompt_version_id: uuid.UUID
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        version = await PromptVersionRepository().by_id(
            session, tenant_id=tenant_id, version_id=prompt_version_id
        )
        if version is None:
            return (
                {},
                [
                    {
                        "field": "prompt_version_id",
                        "resource_id": str(prompt_version_id),
                        "reason": "not found",
                    }
                ],
            )
        prompt = await PromptRepository().by_id(
            session, tenant_id=tenant_id, prompt_id=version.prompt_id
        )
        if prompt is None or prompt.status != PromptStatus.ACTIVE:
            return (
                {},
                [
                    {
                        "field": "prompt_version_id",
                        "resource_id": str(prompt_version_id),
                        "reason": "prompt is archived",
                    }
                ],
            )
        return (
            {
                "prompt_id": str(prompt.id),
                "prompt_code": prompt.code,
                "prompt_version_id": str(version.id),
                "version_no": version.version_no,
                "messages": version.messages,
                "variables": version.variables,
            },
            [],
        )

    async def _resolve_tools(
        self, session, *, tenant_id: uuid.UUID, tool_version_ids: list[uuid.UUID]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        resolved: list[dict[str, Any]] = []
        problems: list[dict[str, Any]] = []
        for tool_version_id in tool_version_ids:
            version = await ToolVersionRepository().by_id(
                session, tenant_id=tenant_id, version_id=tool_version_id
            )
            if version is None:
                problems.append(
                    {
                        "field": "tool_version_ids",
                        "resource_id": str(tool_version_id),
                        "reason": "not found",
                    }
                )
                continue
            tool = await ToolRepository().by_id(session, tenant_id=tenant_id, tool_id=version.tool_id)
            if tool is None or tool.status != ToolStatus.ACTIVE:
                problems.append(
                    {
                        "field": "tool_version_ids",
                        "resource_id": str(tool_version_id),
                        "reason": "tool is disabled",
                    }
                )
                continue
            if (
                await ToolGrantRepository().get(session, tenant_id=tenant_id, tool_id=tool.id)
                is None
            ):
                problems.append(
                    {
                        "field": "tool_version_ids",
                        "resource_id": str(tool_version_id),
                        "reason": "tool is not granted",
                    }
                )
                continue
            resolved.append(
                {
                    "tool_id": str(tool.id),
                    "tool_code": tool.code,
                    "tool_version_id": str(version.id),
                    "version_no": version.version_no,
                    "risk_level": version.risk_level.value,
                    "spec": version.spec,
                }
            )
        return resolved, problems


agent_service = AgentService()
