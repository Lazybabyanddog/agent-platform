"""统一授权判定入口：RBAC 权限点 + 资源授权双判定。

任何 router 都不得自己判断权限，必须经过这里，便于集中审计与后续扩展。
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from agent_platform.contexts.platform.infrastructure.repositories import AuthorizationRepository
from agent_platform.shared.context import ActorContext
from agent_platform.shared.errors import ForbiddenError


class AuthorizationService:
    def __init__(self, session: AsyncSession | None = None) -> None:
        self.session = session
        self.repo = AuthorizationRepository()

    async def require_permission(self, actor: ActorContext, permission: str) -> None:
        if actor.is_admin:
            return
        if not actor.has_permission(permission):
            raise ForbiddenError(f"missing permission: {permission}")

    async def require_resource(
        self,
        actor: ActorContext,
        *,
        permission: str,
        resource_type: str,
        resource_id: uuid.UUID | None = None,
        action: str | None = None,
    ) -> None:
        """要求同时具备权限点与资源授权；管理员跳过资源授权。"""
        await self.require_permission(actor, permission)
        if actor.is_admin or self.session is None:
            return

        action = action or permission.split(":", 1)[-1]
        role_ids = await self.repo.role_ids_for_user(
            self.session, tenant_id=actor.tenant_id, user_id=actor.actor_id
        )
        allowed = await self.repo.has_grant(
            self.session,
            tenant_id=actor.tenant_id,
            resource_type=resource_type,
            resource_id=resource_id,
            user_id=actor.actor_id,
            role_ids=role_ids,
            action=action,
        )
        if not allowed:
            raise ForbiddenError(f"no grant for {resource_type}:{resource_id} action={action}")
