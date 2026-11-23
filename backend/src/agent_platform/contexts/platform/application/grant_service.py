"""资源级授权（ResourceGrant）的维护用例。

权限点解决"能不能做这类操作"，ResourceGrant 解决"能不能对这一个资源做"。
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from agent_platform.contexts.platform.application.audit_service import audit_service
from agent_platform.contexts.platform.domain.enums import GrantSubjectType
from agent_platform.contexts.platform.infrastructure.repositories import AuthorizationRepository
from agent_platform.contexts.platform.infrastructure.tables import ResourceGrant
from agent_platform.shared.context import ActorContext
from agent_platform.shared.db import session_scope
from agent_platform.shared.errors import ConflictError, NotFoundError


class GrantService:
    async def create(
        self,
        *,
        actor: ActorContext,
        resource_type: str,
        resource_id: uuid.UUID,
        subject_type: GrantSubjectType,
        subject_id: uuid.UUID,
        actions: list[str],
    ) -> ResourceGrant:
        async with session_scope(actor.tenant_id) as session:
            existing = await session.scalar(
                select(ResourceGrant).where(
                    ResourceGrant.tenant_id == actor.tenant_id,
                    ResourceGrant.resource_type == resource_type,
                    ResourceGrant.resource_id == resource_id,
                    ResourceGrant.subject_type == subject_type,
                    ResourceGrant.subject_id == subject_id,
                )
            )
            if existing is not None:
                raise ConflictError("grant already exists for this resource and subject")

            grant = await AuthorizationRepository().create_grant(
                session,
                tenant_id=actor.tenant_id,
                resource_type=resource_type,
                resource_id=resource_id,
                subject_type=subject_type,
                subject_id=subject_id,
                actions=actions,
                granted_by=actor.actor_id,
            )
            await audit_service.record(
                session,
                tenant_id=actor.tenant_id,
                action="grant.created",
                resource_type=resource_type,
                resource_id=resource_id,
                actor=actor,
                detail={
                    "subject_type": subject_type.value,
                    "subject_id": str(subject_id),
                    "actions": actions,
                },
            )
            return grant

    async def list(
        self, *, actor: ActorContext, resource_type: str | None = None
    ) -> list[ResourceGrant]:
        async with session_scope(actor.tenant_id) as session:
            return await AuthorizationRepository().list_grants(
                session, tenant_id=actor.tenant_id, resource_type=resource_type
            )

    async def revoke(self, *, actor: ActorContext, grant_id: uuid.UUID) -> None:
        async with session_scope(actor.tenant_id) as session:
            grant = await session.get(ResourceGrant, grant_id)
            if grant is None or grant.tenant_id != actor.tenant_id:
                raise NotFoundError("grant not found")
            resource_type, resource_id = grant.resource_type, grant.resource_id
            deleted = await AuthorizationRepository().delete_grant(
                session, tenant_id=actor.tenant_id, grant_id=grant_id
            )
            if deleted == 0:
                raise NotFoundError("grant not found")
            await audit_service.record(
                session,
                tenant_id=actor.tenant_id,
                action="grant.revoked",
                resource_type=resource_type,
                resource_id=resource_id,
                actor=actor,
                detail={"grant_id": str(grant_id)},
            )


grant_service = GrantService()
