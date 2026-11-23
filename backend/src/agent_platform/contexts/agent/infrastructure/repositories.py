from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from agent_platform.contexts.agent.domain.enums import AgentStatus, AgentType, Environment
from agent_platform.contexts.agent.infrastructure.tables import (
    Agent,
    AgentRelease,
    AgentReleasePointer,
    AgentVersion,
    AgentVersionBinding,
)


class AgentRepository:
    async def by_id(
        self, session: AsyncSession, *, tenant_id: uuid.UUID, agent_id: uuid.UUID
    ) -> Agent | None:
        return await session.scalar(
            select(Agent).where(Agent.tenant_id == tenant_id, Agent.id == agent_id)
        )

    async def by_code(
        self, session: AsyncSession, *, tenant_id: uuid.UUID, code: str
    ) -> Agent | None:
        return await session.scalar(
            select(Agent).where(Agent.tenant_id == tenant_id, Agent.code == code)
        )

    async def list(self, session: AsyncSession, *, tenant_id: uuid.UUID) -> list[Agent]:
        return list(
            await session.scalars(
                select(Agent).where(Agent.tenant_id == tenant_id).order_by(Agent.code)
            )
        )

    @staticmethod
    def _filters(
        *,
        tenant_id: uuid.UUID,
        status: AgentStatus | None,
        agent_type: AgentType | None,
        tag: str | None,
        q: str | None,
    ) -> list[Any]:
        conditions: list[Any] = [Agent.tenant_id == tenant_id]
        if status is not None:
            conditions.append(Agent.status == status)
        if agent_type is not None:
            conditions.append(Agent.type == agent_type)
        if tag:
            conditions.append(Agent.tags.contains([tag]))
        if q:
            pattern = f"%{q.strip()}%"
            conditions.append(or_(Agent.code.ilike(pattern), Agent.name.ilike(pattern)))
        return conditions

    async def list_page(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        status: AgentStatus | None = None,
        agent_type: AgentType | None = None,
        tag: str | None = None,
        q: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[Agent]:
        statement = (
            select(Agent)
            .where(
                *self._filters(
                    tenant_id=tenant_id, status=status, agent_type=agent_type, tag=tag, q=q
                )
            )
            .order_by(Agent.updated_at.desc(), Agent.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(await session.scalars(statement))

    async def count_page(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        status: AgentStatus | None = None,
        agent_type: AgentType | None = None,
        tag: str | None = None,
        q: str | None = None,
    ) -> int:
        statement = select(func.count()).select_from(Agent).where(
            *self._filters(
                tenant_id=tenant_id, status=status, agent_type=agent_type, tag=tag, q=q
            )
        )
        return int(await session.scalar(statement) or 0)

    async def create(self, session: AsyncSession, *, tenant_id: uuid.UUID, **values: Any) -> Agent:
        agent = Agent(tenant_id=tenant_id, **values)
        session.add(agent)
        await session.flush()
        return agent


class AgentVersionRepository:
    async def by_id(
        self, session: AsyncSession, *, tenant_id: uuid.UUID, version_id: uuid.UUID
    ) -> AgentVersion | None:
        return await session.scalar(
            select(AgentVersion).where(
                AgentVersion.tenant_id == tenant_id, AgentVersion.id == version_id
            )
        )

    async def list_for_agent(
        self, session: AsyncSession, *, agent_id: uuid.UUID
    ) -> list[AgentVersion]:
        return list(
            await session.scalars(
                select(AgentVersion)
                .where(AgentVersion.agent_id == agent_id)
                .order_by(AgentVersion.version_no.desc())
            )
        )

    async def latest_for_agent(
        self, session: AsyncSession, *, agent_id: uuid.UUID
    ) -> AgentVersion | None:
        return await session.scalar(
            select(AgentVersion)
            .where(AgentVersion.agent_id == agent_id)
            .order_by(AgentVersion.version_no.desc())
            .limit(1)
        )

    async def count_for_agent(self, session: AsyncSession, *, agent_id: uuid.UUID) -> int:
        return int(
            await session.scalar(
                select(func.count()).select_from(AgentVersion).where(
                    AgentVersion.agent_id == agent_id
                )
            )
            or 0
        )

    async def next_version_no(self, session: AsyncSession, *, agent_id: uuid.UUID) -> int:
        current = await session.scalar(
            select(func.max(AgentVersion.version_no)).where(AgentVersion.agent_id == agent_id)
        )
        return int(current or 0) + 1

    async def create(
        self, session: AsyncSession, *, tenant_id: uuid.UUID, **values: Any
    ) -> AgentVersion:
        version = AgentVersion(tenant_id=tenant_id, **values)
        session.add(version)
        await session.flush()
        return version


class BindingRepository:
    async def create_many(
        self, session: AsyncSession, *, tenant_id: uuid.UUID, bindings: list[dict[str, Any]]
    ) -> None:
        for binding in bindings:
            session.add(AgentVersionBinding(tenant_id=tenant_id, **binding))
        await session.flush()

    async def list_for_version(
        self, session: AsyncSession, *, agent_version_id: uuid.UUID
    ) -> list[AgentVersionBinding]:
        return list(
            await session.scalars(
                select(AgentVersionBinding).where(
                    AgentVersionBinding.agent_version_id == agent_version_id
                )
            )
        )


class ReleaseRepository:
    async def by_id(
        self, session: AsyncSession, *, tenant_id: uuid.UUID, release_id: uuid.UUID
    ) -> AgentRelease | None:
        return await session.scalar(
            select(AgentRelease).where(
                AgentRelease.tenant_id == tenant_id, AgentRelease.id == release_id
            )
        )

    async def list_for_agent(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        agent_id: uuid.UUID,
        env: Environment | None = None,
    ) -> list[AgentRelease]:
        stmt = select(AgentRelease).where(
            AgentRelease.tenant_id == tenant_id, AgentRelease.agent_id == agent_id
        )
        if env is not None:
            stmt = stmt.where(AgentRelease.env == env)
        stmt = stmt.order_by(AgentRelease.released_at.desc())
        return list(await session.scalars(stmt))

    async def list_with_version(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        agent_id: uuid.UUID,
        env: Environment | None = None,
    ) -> list[tuple[AgentRelease, AgentVersion]]:
        stmt = (
            select(AgentRelease, AgentVersion)
            .join(AgentVersion, AgentVersion.id == AgentRelease.agent_version_id)
            .where(AgentRelease.tenant_id == tenant_id, AgentRelease.agent_id == agent_id)
        )
        if env is not None:
            stmt = stmt.where(AgentRelease.env == env)
        stmt = stmt.order_by(AgentRelease.released_at.desc())
        rows = await session.execute(stmt)
        return [(release, version) for release, version in rows.all()]

    async def count_for_agent(
        self, session: AsyncSession, *, tenant_id: uuid.UUID, agent_id: uuid.UUID
    ) -> int:
        return int(
            await session.scalar(
                select(func.count())
                .select_from(AgentRelease)
                .where(AgentRelease.tenant_id == tenant_id, AgentRelease.agent_id == agent_id)
            )
            or 0
        )

    async def find(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        agent_id: uuid.UUID,
        env: Environment,
        agent_version_id: uuid.UUID,
    ) -> AgentRelease | None:
        return await session.scalar(
            select(AgentRelease).where(
                AgentRelease.tenant_id == tenant_id,
                AgentRelease.agent_id == agent_id,
                AgentRelease.env == env,
                AgentRelease.agent_version_id == agent_version_id,
            )
        )

    async def create(
        self, session: AsyncSession, *, tenant_id: uuid.UUID, **values: Any
    ) -> AgentRelease:
        release = AgentRelease(tenant_id=tenant_id, **values)
        session.add(release)
        await session.flush()
        return release


class PointerRepository:
    async def get(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        agent_id: uuid.UUID,
        env: Environment,
    ) -> AgentReleasePointer | None:
        return await session.scalar(
            select(AgentReleasePointer).where(
                AgentReleasePointer.tenant_id == tenant_id,
                AgentReleasePointer.agent_id == agent_id,
                AgentReleasePointer.env == env,
            )
        )

    async def get_with_release(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        agent_id: uuid.UUID,
        env: Environment,
    ) -> tuple[AgentReleasePointer, AgentRelease, AgentVersion] | None:
        stmt = (
            select(AgentReleasePointer, AgentRelease, AgentVersion)
            .join(AgentRelease, AgentRelease.id == AgentReleasePointer.release_id)
            .join(AgentVersion, AgentVersion.id == AgentRelease.agent_version_id)
            .where(
                AgentReleasePointer.tenant_id == tenant_id,
                AgentReleasePointer.agent_id == agent_id,
                AgentReleasePointer.env == env,
            )
        )
        row = (await session.execute(stmt)).first()
        return (row[0], row[1], row[2]) if row else None

    async def list_with_release(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        agent_ids: list[uuid.UUID],
    ) -> list[tuple[AgentReleasePointer, AgentRelease, AgentVersion]]:
        """批量取指针与版本，列表页固定两次查询而非逐行加载。"""
        if not agent_ids:
            return []
        stmt = (
            select(AgentReleasePointer, AgentRelease, AgentVersion)
            .join(AgentRelease, AgentRelease.id == AgentReleasePointer.release_id)
            .join(AgentVersion, AgentVersion.id == AgentRelease.agent_version_id)
            .where(
                AgentReleasePointer.tenant_id == tenant_id,
                AgentReleasePointer.agent_id.in_(agent_ids),
            )
        )
        rows = (await session.execute(stmt)).all()
        return [(row[0], row[1], row[2]) for row in rows]

    async def upsert(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        agent_id: uuid.UUID,
        env: Environment,
        release_id: uuid.UUID,
    ) -> None:
        now = datetime.now(UTC)
        statement = insert(AgentReleasePointer).values(
            tenant_id=tenant_id,
            agent_id=agent_id,
            env=env,
            release_id=release_id,
            created_at=now,
            updated_at=now,
        )
        statement = statement.on_conflict_do_update(
            index_elements=[
                AgentReleasePointer.tenant_id,
                AgentReleasePointer.agent_id,
                AgentReleasePointer.env,
            ],
            set_={"release_id": release_id, "updated_at": now},
        )
        await session.execute(statement)
