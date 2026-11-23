"""同步调用：解析发布版本 → 复检引用 → 校验入参 → 建 run → 执行 → 落结果。"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from agent_platform.contexts.agent.application.release_service import agent_release_service
from agent_platform.contexts.agent.domain.enums import AgentStatus, Environment
from agent_platform.contexts.agent.infrastructure.repositories import (
    AgentRepository,
    AgentVersionRepository,
    PointerRepository,
)
from agent_platform.contexts.agent.infrastructure.tables import AgentRelease, AgentVersion
from agent_platform.contexts.catalog.domain.render import render_messages
from agent_platform.contexts.catalog.infrastructure.repositories import (
    ModelRepository,
    ProviderRepository,
)
from agent_platform.contexts.execution.application.session_service import (
    binding_unavailable,
    session_service,
)
from agent_platform.contexts.execution.domain.enums import (
    CallerType,
    RunErrorCode,
    RunKind,
    RunStatus,
)
from agent_platform.contexts.execution.domain.ports import CallRecorder, ModelPort, ToolPort
from agent_platform.contexts.execution.infrastructure.checkpoint_store import checkpoint_store
from agent_platform.contexts.execution.infrastructure.repositories import (
    CheckpointRepository,
    ExecutionEventRepository,
    RunRepository,
)
from agent_platform.contexts.execution.infrastructure.tool_adapter import CatalogToolPort
from agent_platform.contexts.execution.infrastructure.tracing import (
    TracingModelPort,
    TracingToolPort,
    tool_index,
)
from agent_platform.contexts.execution.runtime.executor import (
    AgentRuntimeError,
    execute_snapshot,
)
from agent_platform.contexts.platform.application.secret_service import secret_service
from agent_platform.settings import get_settings
from agent_platform.shared.context import ActorContext, get_trace_id
from agent_platform.shared.db import session_scope
from agent_platform.shared.errors import (
    ConflictError,
    NotFoundError,
    ResourceUnavailableError,
    SyncTimeoutExceededError,
)

ModelFactory = Callable[[ActorContext, dict[str, Any], float], Awaitable[ModelPort]]
ToolFactory = Callable[[ActorContext], ToolPort]
RecorderFactory = Callable[[ActorContext], CallRecorder]

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class InvocationResult:
    run_id: uuid.UUID
    trace_id: str
    status: RunStatus
    output: str | None
    usage: dict[str, int]
    error: dict[str, Any] | None = None
    checkpoint_id: str | None = None


@dataclass(slots=True)
class InvocationService:
    _model_factory: ModelFactory | None = None
    _tool_factory: ToolFactory | None = None
    _recorder_factory: RecorderFactory | None = None
    _checkpoint_provider: Any = field(default=checkpoint_store)

    async def invoke(
        self,
        *,
        actor: ActorContext,
        agent_id: uuid.UUID,
        env: Environment,
        values: dict[str, Any],
        session_id: str | None = None,
        timeout_s: float | None = None,
    ) -> InvocationResult:
        binding = await session_service.resolve_invoke(
            actor=actor, agent_id=agent_id, env=env, session_key=session_id
        )
        if binding is None:
            release, version = await self._resolve_release(
                actor=actor, agent_id=agent_id, env=env
            )
            snapshot = version.snapshot
            release_id = release.id
            version_id = version.id
            digest = version.config_hash
            thread_id: str | None = None
        else:
            # 绑定会话不再重解析指针；引用复检失败即失败关闭，避免静默换配置
            problems = await self._reference_problems(actor=actor, snapshot=binding.snapshot)
            if problems:
                raise binding_unavailable(problems)
            await session_service.acquire(actor=actor, binding=binding)
            snapshot = binding.snapshot
            release_id = binding.agent_release_id
            version_id = binding.agent_version_id
            digest = binding.config_hash
            thread_id = binding.thread_id

        try:
            return await self.execute_snapshot(
                actor=actor,
                agent_id=agent_id,
                env=env,
                snapshot=snapshot,
                values=values,
                timeout_s=timeout_s,
                kind=RunKind.INVOKE,
                agent_release_id=release_id,
                agent_version_id=version_id,
                config_hash=digest,
                session_id=session_id,
                execution_session_id=binding.session_id if binding else None,
                thread_id=thread_id,
                skip_reference_check=binding is not None,
            )
        finally:
            if binding is not None:
                await session_service.release(actor=actor, binding=binding)

    async def execute_snapshot(
        self,
        *,
        actor: ActorContext,
        agent_id: uuid.UUID,
        env: Environment,
        snapshot: dict[str, Any],
        values: dict[str, Any],
        timeout_s: float | None,
        kind: RunKind,
        agent_release_id: uuid.UUID | None = None,
        agent_version_id: uuid.UUID | None = None,
        config_hash: str | None = None,
        preview_snapshot: dict[str, Any] | None = None,
        session_id: str | None = None,
        execution_session_id: uuid.UUID | None = None,
        thread_id: str | None = None,
        skip_reference_check: bool = False,
    ) -> InvocationResult:
        """发布调用与调试预览共用的执行路径，只有 run 绑定方式不同。"""
        if not skip_reference_check:
            problems = await self._reference_problems(actor=actor, snapshot=snapshot)
            if problems:
                raise ResourceUnavailableError(
                    "referenced resources are not available", details=problems
                )

        # 入参校验失败直接 422，不产生 run
        render_messages(
            snapshot["prompt"]["messages"],
            snapshot["prompt"]["variables"],
            values,
        )

        policy = snapshot["policy"]
        requested = float(timeout_s) if timeout_s is not None else float(policy["timeout_s"])
        if requested > get_settings().sync_max_timeout_s:
            raise SyncTimeoutExceededError(
                "requested timeout exceeds the synchronous invocation limit",
                details=[
                    {
                        "field": "timeout_s",
                        "reason": f"must be <= {get_settings().sync_max_timeout_s}",
                        "code": "ASYNC_REQUIRED",
                    }
                ],
            )
        effective_timeout = min(requested, float(policy["timeout_s"]))

        trace_id = get_trace_id() or str(uuid.uuid4())
        run_id = await self._create_run(
            actor=actor,
            agent_id=agent_id,
            release_id=agent_release_id,
            version_id=agent_version_id,
            env=env,
            values=values,
            session_id=session_id,
            execution_session_id=execution_session_id,
            trace_id=trace_id,
            kind=kind,
            config_hash=config_hash,
            preview_snapshot=preview_snapshot,
        )
        thread_id = thread_id or str(run_id)

        try:
            model = await (self._model_factory or default_model_factory)(
                actor, snapshot, effective_timeout
            )
        except Exception as exc:  # noqa: BLE001 - 失败必须落到 run，不能留下 RUNNING
            error = {
                "code": RunErrorCode.MODEL_ERROR.value,
                "message": "model is not available for execution",
                "details": {"reason": "model_unavailable", "message": str(exc)},
            }
            await self._finish_failed(actor=actor, run_id=run_id, error=error)
            return InvocationResult(
                run_id=run_id,
                trace_id=trace_id,
                status=RunStatus.FAILED,
                output=None,
                usage={"steps": 0, "tokens": 0},
                error=error,
            )
        tool_port = (self._tool_factory or default_tool_factory)(actor)
        try:
            checkpointer = await self._checkpoint_provider.saver()
        except Exception as exc:  # noqa: BLE001 - checkpoint 属于辅助能力，不阻断执行
            logger.warning("checkpointer unavailable, continuing without it: %s", exc)
            checkpointer = None

        # 轨迹记录挂在端口外层：失败不影响执行，且同一次运行共享 trace_id
        recorder = (self._recorder_factory or default_recorder_factory)(actor)
        model = TracingModelPort(
            model,
            recorder=recorder,
            run_id=run_id,
            trace_id=trace_id,
            model_id=uuid.UUID(str(snapshot["model"]["model_id"])),
            model_name=str(snapshot["model"]["model_name"]),
        )
        tool_port = TracingToolPort(
            tool_port,
            recorder=recorder,
            run_id=run_id,
            trace_id=trace_id,
            tools=tool_index(snapshot),
        )

        try:
            result = await execute_snapshot(
                snapshot=snapshot,
                model=model,
                tools=tool_port,
                input_values=values,
                timeout_s=effective_timeout,
                checkpointer=checkpointer,
                thread_id=thread_id,
            )
        except AgentRuntimeError as exc:
            error = {
                "code": exc.code.value,
                "message": exc.message,
                "details": exc.details,
            }
            await self._finish_failed(actor=actor, run_id=run_id, error=error)
            return InvocationResult(
                run_id=run_id,
                trace_id=trace_id,
                status=RunStatus.FAILED,
                output=None,
                usage={
                    "steps": int(exc.details.get("used_steps") or 0),
                    "tokens": int(exc.details.get("used_tokens") or 0),
                },
                error=error,
            )

        await self._finish_success(
            actor=actor,
            run_id=run_id,
            result=result,
            thread_id=thread_id,
            history_token_limit=int(policy.get("history_token_limit") or 4000),
        )
        return InvocationResult(
            run_id=run_id,
            trace_id=trace_id,
            status=RunStatus.SUCCESS,
            output=result.output,
            usage={"steps": result.used_steps, "tokens": result.used_tokens},
            checkpoint_id=result.checkpoint_id,
        )

    # ------------------------------------------------------------------

    async def _resolve_release(
        self, *, actor: ActorContext, agent_id: uuid.UUID, env: Environment
    ) -> tuple[AgentRelease, AgentVersion]:
        """受理时一次性固定发布记录与版本，后续步骤不再读指针。"""
        async with session_scope(actor.tenant_id) as session:
            agent = await AgentRepository().by_id(
                session, tenant_id=actor.tenant_id, agent_id=agent_id
            )
            if agent is None:
                raise NotFoundError("agent not found")
            # 状态门禁先于指针读取：停用/归档立即失败关闭，错误码可区分。
            # DRAFT 尚无发布，继续走指针查询以保留 404 语义。
            if agent.status == AgentStatus.ARCHIVED:
                raise ConflictError("agent is archived", code="AGENT_ARCHIVED")
            if agent.status == AgentStatus.INACTIVE:
                raise ConflictError(
                    "agent is not active",
                    code="AGENT_INACTIVE",
                    details=[{"field": "status", "current": agent.status.value}],
                )
            found = await PointerRepository().get_with_release(
                session, tenant_id=actor.tenant_id, agent_id=agent_id, env=env
            )
            if found is None:
                raise NotFoundError("agent is not published in this environment")

            _, release, _ = found
            version = await AgentVersionRepository().by_id(
                session, tenant_id=actor.tenant_id, version_id=release.agent_version_id
            )
            if version is None:
                raise NotFoundError("agent version not found")
            return release, version

    async def _reference_problems(
        self, *, actor: ActorContext, snapshot: dict[str, Any]
    ) -> list[dict[str, Any]]:
        async with session_scope(actor.tenant_id) as session:
            # 调用路径不复检模型/供应商状态：已发布版本按快照继续运行（D7）
            return await agent_release_service.reference_problems(
                session,
                tenant_id=actor.tenant_id,
                snapshot=snapshot,
                check_model_status=False,
            )

    async def _create_run(
        self,
        *,
        actor: ActorContext,
        agent_id: uuid.UUID,
        release_id: uuid.UUID | None,
        version_id: uuid.UUID | None,
        env: Environment,
        values: dict[str, Any],
        session_id: str | None,
        execution_session_id: uuid.UUID | None,
        trace_id: str,
        kind: RunKind,
        config_hash: str | None,
        preview_snapshot: dict[str, Any] | None,
    ) -> uuid.UUID:
        async with session_scope(actor.tenant_id) as session:
            run = await RunRepository().create(
                session,
                tenant_id=actor.tenant_id,
                agent_id=agent_id,
                agent_release_id=release_id,
                agent_version_id=version_id,
                env=env,
                kind=kind,
                config_hash=config_hash,
                preview_snapshot=preview_snapshot,
                caller_type=(
                    CallerType.API_KEY if actor.actor_type == "API_KEY" else CallerType.USER
                ),
                caller_id=actor.actor_id,
                session_id=session_id,
                execution_session_id=execution_session_id,
                input=values,
                status=RunStatus.RUNNING,
                trace_id=trace_id,
            )
            return run.id

    async def _finish_success(
        self,
        *,
        actor: ActorContext,
        run_id: uuid.UUID,
        result,
        thread_id: str,
        history_token_limit: int,
    ) -> None:
        async with session_scope(actor.tenant_id) as session:
            run = await RunRepository().by_id(
                session, tenant_id=actor.tenant_id, run_id=run_id
            )
            if run is None:
                return
            await RunRepository().finish(
                session, run, status=RunStatus.SUCCESS, finished_at=datetime.now(UTC)
            )
            if result.checkpoint_id:
                await CheckpointRepository().record(
                    session,
                    tenant_id=actor.tenant_id,
                    run_id=run_id,
                    thread_id=thread_id,
                    checkpoint_id=result.checkpoint_id,
                )
            if getattr(result, "history_trimmed", 0):
                await ExecutionEventRepository().append(
                    session,
                    tenant_id=actor.tenant_id,
                    run_id=run_id,
                    type="session.history_trimmed",
                    payload={
                        "dropped": result.history_trimmed,
                        "kept": getattr(result, "history_kept", 0),
                        "limit": history_token_limit,
                    },
                )
            await ExecutionEventRepository().append(
                session,
                tenant_id=actor.tenant_id,
                run_id=run_id,
                type="run.completed",
                payload={
                    "output": result.output,
                    "usage": {"steps": result.used_steps, "tokens": result.used_tokens},
                },
            )

    async def _finish_failed(
        self, *, actor: ActorContext, run_id: uuid.UUID, error: dict[str, Any]
    ) -> None:
        async with session_scope(actor.tenant_id) as session:
            run = await RunRepository().by_id(
                session, tenant_id=actor.tenant_id, run_id=run_id
            )
            if run is None:
                return
            await RunRepository().finish(
                session,
                run,
                status=RunStatus.FAILED,
                finished_at=datetime.now(UTC),
                error=error,
            )
            await ExecutionEventRepository().append(
                session,
                tenant_id=actor.tenant_id,
                run_id=run_id,
                type="run.failed",
                payload=error,
            )


def _effective_client_timeout(snapshot: dict[str, Any], timeout_s: float) -> float:
    """模型级 ``params.timeout`` 只能收紧客户端超时，永远受本次运行预算约束。"""
    params = snapshot["model"].get("params") or {}
    configured = params.get("timeout")
    if isinstance(configured, (int, float)) and not isinstance(configured, bool):
        if configured > 0:
            return min(float(configured), float(timeout_s))
    return float(timeout_s)


async def default_model_factory(
    actor: ActorContext, snapshot: dict[str, Any], timeout_s: float
) -> ModelPort:
    from agent_platform.contexts.execution.infrastructure.llm.langchain_adapter import (
        LangChainModelAdapter,
    )

    async with session_scope(actor.tenant_id) as session:
        model = await ModelRepository().by_id(
            session,
            tenant_id=actor.tenant_id,
            model_id=uuid.UUID(str(snapshot["model"]["model_id"])),
        )
        if model is None:
            raise ResourceUnavailableError("model is no longer available")
        provider = await ProviderRepository().by_id(
            session, tenant_id=actor.tenant_id, provider_id=model.provider_id
        )
        if provider is None:
            raise ResourceUnavailableError("model provider is no longer available")

    api_key = "not-configured"
    if provider.secret_ref_id is not None:
        api_key = await secret_service.resolve(
            tenant_id=actor.tenant_id, secret_id=provider.secret_ref_id
        )
    return LangChainModelAdapter(
        base_url=provider.base_url,
        api_key=api_key,
        timeout_s=_effective_client_timeout(snapshot, timeout_s),
    )


def default_tool_factory(actor: ActorContext) -> ToolPort:
    return CatalogToolPort(actor=actor)


def default_recorder_factory(actor: ActorContext) -> CallRecorder:
    from agent_platform.contexts.insight.application.recorder import InsightCallRecorder

    return InsightCallRecorder(tenant_id=actor.tenant_id)


invocation_service = InvocationService()
