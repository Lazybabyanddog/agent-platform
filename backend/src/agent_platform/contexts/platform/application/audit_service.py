"""审计：只追加写入，读写都在租户范围内。

同一事务内的业务失败要留痕，因此既提供"借用调用方事务"的 record()，
也提供"自己开事务"的 record_detached()。
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_platform.contexts.platform.domain.enums import ActorType, AuditResult
from agent_platform.contexts.platform.infrastructure.tables import AuditLog
from agent_platform.shared.context import ActorContext, get_trace_id
from agent_platform.shared.db import session_scope


class AuditService:
    async def record(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        action: str,
        resource_type: str,
        resource_id: uuid.UUID | None = None,
        actor: ActorContext | None = None,
        result: AuditResult = AuditResult.SUCCESS,
        detail: dict[str, Any] | None = None,
    ) -> AuditLog:
        entry = AuditLog(
            tenant_id=tenant_id,
            actor_type=ActorType(actor.actor_type) if actor else ActorType.SYSTEM,
            actor_id=actor.actor_id if actor else None,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            result=result,
            detail=detail,
            trace_id=get_trace_id(),
        )
        session.add(entry)
        await session.flush()
        return entry

    async def record_detached(
        self,
        *,
        tenant_id: uuid.UUID,
        action: str,
        resource_type: str,
        resource_id: uuid.UUID | None = None,
        actor: ActorContext | None = None,
        result: AuditResult = AuditResult.FAILURE,
        detail: dict[str, Any] | None = None,
    ) -> None:
        async with session_scope(tenant_id) as session:
            await self.record(
                session,
                tenant_id=tenant_id,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                actor=actor,
                result=result,
                detail=detail,
            )

    async def list_entries(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        resource_type: str | None = None,
        resource_id: uuid.UUID | None = None,
        action: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[AuditLog]:
        stmt = select(AuditLog).where(AuditLog.tenant_id == tenant_id)
        if resource_type:
            stmt = stmt.where(AuditLog.resource_type == resource_type)
        if resource_id:
            stmt = stmt.where(AuditLog.resource_id == resource_id)
        if action:
            stmt = stmt.where(AuditLog.action == action)
        stmt = stmt.order_by(AuditLog.created_at.desc()).limit(limit).offset(offset)
        result = await session.scalars(stmt)
        return list(result)

    async def count_entries(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        resource_type: str | None = None,
        resource_id: uuid.UUID | None = None,
        action: str | None = None,
    ) -> int:
        stmt = select(func.count()).select_from(AuditLog).where(AuditLog.tenant_id == tenant_id)
        if resource_type:
            stmt = stmt.where(AuditLog.resource_type == resource_type)
        if resource_id:
            stmt = stmt.where(AuditLog.resource_id == resource_id)
        if action:
            stmt = stmt.where(AuditLog.action == action)
        return int(await session.scalar(stmt) or 0)


audit_service = AuditService()
