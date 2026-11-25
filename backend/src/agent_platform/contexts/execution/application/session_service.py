"""多轮会话寻址：首轮固化绑定，后续轮复用同一绑定与 thread。

正式会话绑定发布记录与版本，预览会话绑定首轮的临时快照；两者共用同一张表，
靠 `kind` 区分唯一键，因此同样的会话标识不会串上下文。
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from agent_platform.contexts.agent.domain.enums import AgentStatus, Environment
from agent_platform.contexts.agent.infrastructure.repositories import (
    AgentRepository,
    AgentVersionRepository,
    PointerRepository,
)
from agent_platform.contexts.agent.infrastructure.tables import Agent
from agent_platform.contexts.execution.domain.enums import RunKind
from agent_platform.contexts.execution.infrastructure.repositories import (
    ExecutionSessionRepository,
)
from agent_platform.shared.context import ActorContext
from agent_platform.shared.db import session_scope
from agent_platform.shared.errors import (
    ConflictError,
    NotFoundError,
    ResourceUnavailableError,
    ValidationFailedError,
)
from agent_platform.shared.hashing import config_hash as compute_config_hash

SESSION_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
# BUSY 超过该时长视为上一个持有者已崩溃，允许抢占
STALE_BUSY_SECONDS = 180


@dataclass(frozen=True, slots=True)
class SessionBinding:
    session_id: uuid.UUID
    thread_id: str
    snapshot: dict[str, Any]
    agent_release_id: uuid.UUID | None
    agent_version_id: uuid.UUID | None
    config_hash: str | None
    expires_at: datetime
    history_token_limit: int
    reused: bool


def gate_agent(agent: Agent) -> None:
    """调用受理前的状态门禁：软删除/归档/停用立即失败关闭。"""
    if agent.deleted_at is not None:
        raise ConflictError("agent has been deleted", code="AGENT_DELETED")
    if agent.status == AgentStatus.ARCHIVED:
        raise ConflictError("agent is archived", code="AGENT_ARCHIVED")
    if agent.status == AgentStatus.INACTIVE:
        raise ConflictError(
            "agent is not active",
            code="AGENT_INACTIVE",
            details=[{"field": "status", "current": agent.status.value}],
        )


def binding_unavailable(problems: list[dict[str, Any]]) -> ResourceUnavailableError:
    """绑定失效：不自动切换版本，提示调用方新建会话重新绑定。"""
    return ResourceUnavailableError(
        "session binding is no longer valid; create a new session",
        code="SESSION_BINDING_UNAVAILABLE",
        details=[
            *problems,
            {
                "field": "session_id",
                "reason": "session binding is stale",
                "hint": "start a new session to bind the current release",
            },
        ],
    )


def _ttl(snapshot: dict[str, Any], now: datetime) -> datetime:
    hours = int((snapshot.get("policy") or {}).get("session_ttl_h") or 168)
    return now + timedelta(hours=hours)


def _history_limit(snapshot: dict[str, Any]) -> int:
    return int((snapshot.get("policy") or {}).get("history_token_limit") or 4000)


TITLE_MAX_LENGTH = 60


def session_title(values: dict[str, Any]) -> str | None:
    """从首轮输入取最长可读文本作为会话标题：折叠空白后截断。"""
    candidates = [
        value for value in values.values() if isinstance(value, str) and value.strip()
    ]
    if not candidates:
        return None
    text = " ".join(max(candidates, key=len).split())
    return text[:TITLE_MAX_LENGTH] or None


class SessionService:
    """会话寻址与占用管理。"""

    def __init__(self) -> None:
        self._repository = ExecutionSessionRepository()

    async def resolve_invoke(
        self,
        *,
        actor: ActorContext,
        agent_id: uuid.UUID,
        env: Environment,
        session_key: str | None,
    ) -> SessionBinding | None:
        """首轮解析发布指针并落绑定；后续轮只读绑定，不再看指针。"""
        if session_key is None:
            return None
        key = self._validate_key(session_key)

        async with session_scope(actor.tenant_id) as session:
            agent = await AgentRepository().by_id(
                session, tenant_id=actor.tenant_id, agent_id=agent_id
            )
            if agent is None:
                raise NotFoundError("agent not found")
            gate_agent(agent)

            repo = self._repository
            now = datetime.now(UTC)
            existing = await repo.by_scope(
                session,
                tenant_id=actor.tenant_id,
                agent_id=agent_id,
                env=env,
                kind=RunKind.INVOKE,
                session_key=key,
            )
            if existing is not None and existing.expires_at <= now:
                # 过期即视为新会话：换新行、换 thread，旧上下文不再参与
                await repo.delete(session, existing)
                existing = None

            if existing is not None:
                version = await AgentVersionRepository().by_id(
                    session,
                    tenant_id=actor.tenant_id,
                    version_id=existing.agent_version_id,
                )
                if version is None:
                    raise binding_unavailable(
                        [
                            {
                                "field": "agent_version_id",
                                "resource_id": str(existing.agent_version_id),
                                "reason": "not found",
                            }
                        ]
                    )
                return SessionBinding(
                    session_id=existing.id,
                    thread_id=f"sess-{existing.id}",
                    snapshot=version.snapshot,
                    agent_release_id=existing.agent_release_id,
                    agent_version_id=existing.agent_version_id,
                    config_hash=version.config_hash,
                    expires_at=existing.expires_at,
                    history_token_limit=_history_limit(version.snapshot),
                    reused=True,
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

            row = await repo.create(
                session,
                tenant_id=actor.tenant_id,
                agent_id=agent_id,
                env=env,
                kind=RunKind.INVOKE,
                session_key=key,
                agent_release_id=release.id,
                agent_version_id=version.id,
                expires_at=_ttl(version.snapshot, now),
                created_by=actor.actor_id,
            )
            return SessionBinding(
                session_id=row.id,
                thread_id=f"sess-{row.id}",
                snapshot=version.snapshot,
                agent_release_id=release.id,
                agent_version_id=version.id,
                config_hash=version.config_hash,
                expires_at=row.expires_at,
                history_token_limit=_history_limit(version.snapshot),
                reused=False,
            )

    async def resolve_preview(
        self,
        *,
        actor: ActorContext,
        agent_id: uuid.UUID,
        env: Environment,
        session_key: str | None,
        assemble: Callable[[AsyncSession], Awaitable[tuple[dict[str, Any], str]]],
    ) -> SessionBinding | None:
        """预览首轮固化临时配置；后续轮忽略编辑态，始终用首轮快照。"""
        if session_key is None:
            return None
        key = self._validate_key(session_key)

        async with session_scope(actor.tenant_id) as session:
            repo = self._repository
            now = datetime.now(UTC)
            existing = await repo.by_scope(
                session,
                tenant_id=actor.tenant_id,
                agent_id=agent_id,
                env=env,
                kind=RunKind.PREVIEW,
                session_key=key,
            )
            if existing is not None and existing.expires_at <= now:
                await repo.delete(session, existing)
                existing = None

            if existing is not None:
                snapshot = dict(existing.preview_snapshot or {})
                return SessionBinding(
                    session_id=existing.id,
                    thread_id=f"sess-{existing.id}",
                    snapshot=snapshot,
                    agent_release_id=None,
                    agent_version_id=None,
                    config_hash=compute_config_hash(snapshot) if snapshot else None,
                    expires_at=existing.expires_at,
                    history_token_limit=_history_limit(snapshot),
                    reused=True,
                )

            snapshot, digest = await assemble(session)
            row = await repo.create(
                session,
                tenant_id=actor.tenant_id,
                agent_id=agent_id,
                env=env,
                kind=RunKind.PREVIEW,
                session_key=key,
                preview_snapshot=snapshot,
                expires_at=_ttl(snapshot, now),
                created_by=actor.actor_id,
            )
            return SessionBinding(
                session_id=row.id,
                thread_id=f"sess-{row.id}",
                snapshot=snapshot,
                agent_release_id=None,
                agent_version_id=None,
                config_hash=digest,
                expires_at=row.expires_at,
                history_token_limit=_history_limit(snapshot),
                reused=False,
            )

    async def acquire(self, *, actor: ActorContext, binding: SessionBinding) -> None:
        async with session_scope(actor.tenant_id) as session:
            now = datetime.now(UTC)
            acquired = await self._repository.acquire(
                session,
                tenant_id=actor.tenant_id,
                session_id=binding.session_id,
                now=now,
                stale_before=now - timedelta(seconds=STALE_BUSY_SECONDS),
            )
        if not acquired:
            raise ConflictError(
                "session is processing another turn",
                code="SESSION_BUSY",
                details=[
                    {
                        "field": "session_id",
                        "reason": "another turn is in progress; retry after it completes",
                    }
                ],
            )

    async def release(self, *, actor: ActorContext, binding: SessionBinding) -> None:
        now = datetime.now(UTC)
        async with session_scope(actor.tenant_id) as session:
            await self._repository.release(
                session,
                tenant_id=actor.tenant_id,
                session_id=binding.session_id,
                now=now,
                expires_at=_ttl(binding.snapshot, now),
            )

    async def record_turn(
        self,
        *,
        actor: ActorContext,
        binding: SessionBinding,
        values: dict[str, Any],
    ) -> None:
        """一轮产生运行后记账：累加轮数，首轮写入标题。

        标题是首轮输入的截断，用于会话列表辨识。
        """
        title = session_title(values)
        async with session_scope(actor.tenant_id) as session:
            await self._repository.record_turn(
                session,
                tenant_id=actor.tenant_id,
                session_id=binding.session_id,
                title=title,
                now=datetime.now(UTC),
            )

    def _validate_key(self, session_key: str) -> str:
        key = session_key.strip()
        if not SESSION_KEY_PATTERN.fullmatch(key):
            raise ValidationFailedError.for_field(
                "session_id",
                "must be 1-128 chars of letters, digits, dot, colon, underscore or hyphen",
            )
        return key


session_service = SessionService()
