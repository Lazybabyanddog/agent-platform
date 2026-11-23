"""execution 上下文的仓储。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agent_platform.contexts.agent.domain.enums import Environment
from agent_platform.contexts.agent.infrastructure.tables import AgentVersion
from agent_platform.contexts.execution.domain.enums import RunKind, RunStatus, SessionStatus
from agent_platform.contexts.execution.infrastructure.tables import (
    CheckpointRef,
    ExecutionEvent,
    ExecutionSession,
    Run,
)


class RunRepository:
    async def create(
        self, session: AsyncSession, *, tenant_id: uuid.UUID, **values: Any
    ) -> Run:
        run = Run(tenant_id=tenant_id, **values)
        session.add(run)
        await session.flush()
        return run

    async def by_id(
        self, session: AsyncSession, *, tenant_id: uuid.UUID, run_id: uuid.UUID
    ) -> Run | None:
        return await session.scalar(
            select(Run).where(Run.tenant_id == tenant_id, Run.id == run_id)
        )

    async def count_for_agent(
        self, session: AsyncSession, *, tenant_id: uuid.UUID, agent_id: uuid.UUID
    ) -> int:
        return int(
            await session.scalar(
                select(func.count())
                .select_from(Run)
                .where(Run.tenant_id == tenant_id, Run.agent_id == agent_id)
            )
            or 0
        )

    async def latest_preview_for_config(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        agent_id: uuid.UUID,
        config_hash: str,
    ) -> Run | None:
        return await session.scalar(
            select(Run)
            .where(
                Run.tenant_id == tenant_id,
                Run.agent_id == agent_id,
                Run.kind == RunKind.PREVIEW,
                Run.config_hash == config_hash,
            )
            .order_by(Run.started_at.desc())
            .limit(1)
        )

    async def list_runs(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        agent_id: uuid.UUID | None = None,
        kind: RunKind | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Run], int]:
        conditions: list[Any] = [Run.tenant_id == tenant_id]
        if agent_id is not None:
            conditions.append(Run.agent_id == agent_id)
        if kind is not None:
            conditions.append(Run.kind == kind)
        if start is not None:
            conditions.append(Run.started_at >= start)
        if end is not None:
            conditions.append(Run.started_at < end)

        total = int(
            await session.scalar(select(func.count()).select_from(Run).where(*conditions)) or 0
        )
        rows = await session.scalars(
            select(Run)
            .where(*conditions)
            .order_by(Run.started_at.desc(), Run.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(rows), total

    async def finish(
        self,
        session: AsyncSession,
        run: Run,
        *,
        status: RunStatus,
        finished_at: datetime,
        error: dict[str, Any] | None = None,
    ) -> None:
        run.status = status
        run.finished_at = finished_at
        run.error = error


class ExecutionSessionRepository:
    """会话唯一键寻址、运行抢占与活跃时间维护。

    抢占用条件更新实现（IDLE 或 BUSY 但已超时），并发下只有一条语句能成功，
    不需要额外加锁；会话之间互不影响。
    """

    async def by_scope(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        agent_id: uuid.UUID,
        env: Environment,
        kind: RunKind,
        session_key: str,
    ) -> ExecutionSession | None:
        return await session.scalar(
            select(ExecutionSession).where(
                ExecutionSession.tenant_id == tenant_id,
                ExecutionSession.agent_id == agent_id,
                ExecutionSession.env == env,
                ExecutionSession.kind == kind,
                ExecutionSession.session_key == session_key,
            )
        )

    async def by_id(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        session_id: uuid.UUID,
    ) -> ExecutionSession | None:
        return await session.scalar(
            select(ExecutionSession).where(
                ExecutionSession.tenant_id == tenant_id, ExecutionSession.id == session_id
            )
        )

    async def create(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        agent_id: uuid.UUID,
        env: Environment,
        kind: RunKind,
        session_key: str,
        expires_at: datetime,
        agent_release_id: uuid.UUID | None = None,
        agent_version_id: uuid.UUID | None = None,
        preview_snapshot: dict[str, Any] | None = None,
    ) -> ExecutionSession:
        row = ExecutionSession(
            tenant_id=tenant_id,
            agent_id=agent_id,
            env=env,
            kind=kind,
            session_key=session_key,
            agent_release_id=agent_release_id,
            agent_version_id=agent_version_id,
            preview_snapshot=preview_snapshot,
            status=SessionStatus.IDLE,
            last_active_at=datetime.now(tz=expires_at.tzinfo),
            expires_at=expires_at,
        )
        session.add(row)
        await session.flush()
        return row

    async def acquire(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        session_id: uuid.UUID,
        now: datetime,
        stale_before: datetime,
    ) -> bool:
        """IDLE → BUSY；BUSY 且上次占用已超过 stale_before 视为超时抢占。"""
        statement = (
            update(ExecutionSession)
            .where(
                ExecutionSession.tenant_id == tenant_id,
                ExecutionSession.id == session_id,
                or_(
                    ExecutionSession.status == SessionStatus.IDLE,
                    ExecutionSession.busy_since.is_(None),
                    ExecutionSession.busy_since < stale_before,
                ),
            )
            .values(status=SessionStatus.BUSY, busy_since=now)
            .execution_options(synchronize_session=False)
        )
        result = await session.execute(statement)
        return result.rowcount == 1

    async def release(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        session_id: uuid.UUID,
        now: datetime,
        expires_at: datetime,
    ) -> None:
        statement = (
            update(ExecutionSession)
            .where(
                ExecutionSession.tenant_id == tenant_id,
                ExecutionSession.id == session_id,
            )
            .values(
                status=SessionStatus.IDLE,
                busy_since=None,
                last_active_at=now,
                expires_at=expires_at,
            )
            .execution_options(synchronize_session=False)
        )
        await session.execute(statement)

    async def delete(
        self, session: AsyncSession, row: ExecutionSession
    ) -> None:
        await session.delete(row)
        await session.flush()

    async def list_for_agent(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        agent_id: uuid.UUID,
        kind: RunKind | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[tuple[ExecutionSession, int | None]], int]:
        conditions: list[Any] = [
            ExecutionSession.tenant_id == tenant_id,
            ExecutionSession.agent_id == agent_id,
        ]
        if kind is not None:
            conditions.append(ExecutionSession.kind == kind)
        if start is not None:
            conditions.append(ExecutionSession.last_active_at >= start)
        if end is not None:
            conditions.append(ExecutionSession.last_active_at < end)

        total = int(
            await session.scalar(
                select(func.count()).select_from(ExecutionSession).where(*conditions)
            )
            or 0
        )
        rows = await session.execute(
            select(ExecutionSession, AgentVersion.version_no)
            .outerjoin(AgentVersion, AgentVersion.id == ExecutionSession.agent_version_id)
            .where(*conditions)
            .order_by(ExecutionSession.last_active_at.desc(), ExecutionSession.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return [(row[0], row[1]) for row in rows], total


class ExecutionEventRepository:
    async def next_seq(self, session: AsyncSession, *, run_id: uuid.UUID) -> int:
        current = await session.scalar(
            select(func.max(ExecutionEvent.seq)).where(ExecutionEvent.run_id == run_id)
        )
        return int(current or 0) + 1

    async def append(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
        type: str,
        payload: dict[str, Any],
    ) -> ExecutionEvent:
        event = ExecutionEvent(
            tenant_id=tenant_id,
            run_id=run_id,
            seq=await self.next_seq(session, run_id=run_id),
            type=type,
            payload=payload,
        )
        session.add(event)
        await session.flush()
        return event

    async def list_for_run(
        self, session: AsyncSession, *, tenant_id: uuid.UUID, run_id: uuid.UUID
    ) -> list[ExecutionEvent]:
        return list(
            await session.scalars(
                select(ExecutionEvent)
                .where(ExecutionEvent.tenant_id == tenant_id, ExecutionEvent.run_id == run_id)
                .order_by(ExecutionEvent.seq)
            )
        )


class CheckpointRepository:
    async def record(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
        thread_id: str,
        checkpoint_id: str,
    ) -> CheckpointRef:
        ref = CheckpointRef(
            tenant_id=tenant_id,
            run_id=run_id,
            thread_id=thread_id,
            checkpoint_id=checkpoint_id,
        )
        session.add(ref)
        await session.flush()
        return ref

    async def by_run(
        self, session: AsyncSession, *, tenant_id: uuid.UUID, run_id: uuid.UUID
    ) -> CheckpointRef | None:
        return await session.scalar(
            select(CheckpointRef).where(
                CheckpointRef.tenant_id == tenant_id, CheckpointRef.run_id == run_id
            )
        )
