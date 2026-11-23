"""租户与成员管理。"""

from __future__ import annotations

import uuid

from agent_platform.contexts.platform.application.audit_service import audit_service
from agent_platform.contexts.platform.application.defaults import ensure_platform_defaults
from agent_platform.contexts.platform.domain.enums import MembershipStatus, TenantStatus
from agent_platform.contexts.platform.infrastructure.repositories import (
    AuthorizationRepository,
    MembershipRepository,
    TenantRepository,
    UserRepository,
)
from agent_platform.contexts.platform.infrastructure.tables import Membership, Tenant
from agent_platform.shared.context import ActorContext
from agent_platform.shared.db import session_scope, system_session_scope
from agent_platform.shared.errors import ConflictError, ForbiddenError, NotFoundError


class TenantService:
    async def create(
        self,
        *,
        code: str,
        name: str,
        description: str | None = None,
        actor: ActorContext | None = None,
    ) -> Tenant:
        async with system_session_scope() as session:
            await ensure_platform_defaults(session)
            repo = TenantRepository()
            if await repo.by_code(session, code):
                raise ConflictError(f"tenant code already exists: {code}")
            tenant = await repo.create(session, code=code, name=name, description=description)
            await audit_service.record(
                session,
                tenant_id=tenant.id,
                action="tenant.created",
                resource_type="tenant",
                resource_id=tenant.id,
                actor=actor,
                detail={"code": code},
            )
            return tenant

    async def find_by_code(self, code: str) -> Tenant | None:
        async with system_session_scope() as session:
            return await TenantRepository().by_code(session, code)

    async def get(self, tenant_id: uuid.UUID) -> Tenant:
        async with system_session_scope() as session:
            tenant = await TenantRepository().by_id(session, tenant_id)
            if tenant is None:
                raise NotFoundError("tenant not found")
            return tenant

    async def list(self, *, limit: int = 50, offset: int = 0) -> list[Tenant]:
        async with system_session_scope() as session:
            return await TenantRepository().list(session, limit=limit, offset=offset)

    async def set_status(
        self, *, tenant_id: uuid.UUID, status: TenantStatus, actor: ActorContext
    ) -> None:
        async with system_session_scope() as session:
            tenant = await TenantRepository().by_id(session, tenant_id)
            if tenant is None:
                raise NotFoundError("tenant not found")
            tenant.status = status
            await audit_service.record(
                session,
                tenant_id=tenant_id,
                action=f"tenant.{status.value.lower()}",
                resource_type="tenant",
                resource_id=tenant_id,
                actor=actor,
            )

    async def set_content_retention(
        self, *, tenant_id: uuid.UUID, enabled: bool, actor: ActorContext
    ) -> Tenant:
        async with system_session_scope() as session:
            tenant = await TenantRepository().by_id(session, tenant_id)
            if tenant is None:
                raise NotFoundError("tenant not found")
            tenant.content_retention_enabled = enabled
            await audit_service.record(
                session,
                tenant_id=tenant_id,
                action=f"tenant.insight_retention_{'enabled' if enabled else 'disabled'}",
                resource_type="tenant",
                resource_id=tenant_id,
                actor=actor,
                detail={"content_retention_enabled": enabled},
            )
            return tenant

    async def content_retention_enabled(self, tenant_id: uuid.UUID) -> bool:
        async with system_session_scope() as session:
            tenant = await TenantRepository().by_id(session, tenant_id)
            return bool(tenant is not None and tenant.content_retention_enabled)

    async def ensure_active(self, tenant_id: uuid.UUID) -> Tenant:
        async with system_session_scope() as session:
            tenant = await TenantRepository().by_id(session, tenant_id)
        if tenant is None:
            raise NotFoundError("tenant not found")
        if tenant.status != TenantStatus.ACTIVE:
            raise ForbiddenError(f"tenant is {tenant.status.value.lower()}")
        return tenant

    async def add_member(
        self,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        actor: ActorContext,
        department: str | None = None,
        role_code: str = "member",
    ) -> Membership:
        async with session_scope(tenant_id) as session:
            user = await UserRepository().by_id(session, user_id)
            if user is None:
                raise NotFoundError("user not found")

            repo = MembershipRepository()
            existing = await repo.get(session, tenant_id=tenant_id, user_id=user_id)
            if existing is not None:
                if existing.status == MembershipStatus.DISABLED:
                    existing.status = MembershipStatus.ACTIVE
                    return existing
                raise ConflictError("user is already a member of this tenant")

            membership = await repo.create(
                session, tenant_id=tenant_id, user_id=user_id, department=department
            )
            authz_repo = AuthorizationRepository()
            role = await authz_repo.role_by_code(session, tenant_id=tenant_id, code=role_code)
            if role is not None:
                await authz_repo.assign_role(
                    session, tenant_id=tenant_id, user_id=user_id, role_id=role.id
                )
            await audit_service.record(
                session,
                tenant_id=tenant_id,
                action="membership.created",
                resource_type="membership",
                resource_id=membership.id,
                actor=actor,
                detail={"user_id": str(user_id), "role": role_code},
            )
            return membership

    async def disable_member(
        self, *, tenant_id: uuid.UUID, membership_id: uuid.UUID, actor: ActorContext
    ) -> None:
        async with session_scope(tenant_id) as session:
            membership = await session.get(Membership, membership_id)
            if membership is None or membership.tenant_id != tenant_id:
                raise NotFoundError("membership not found")
            membership.status = MembershipStatus.DISABLED
            await audit_service.record(
                session,
                tenant_id=tenant_id,
                action="membership.disabled",
                resource_type="membership",
                resource_id=membership.id,
                actor=actor,
            )

    async def list_members(self, tenant_id: uuid.UUID) -> list[Membership]:
        async with session_scope(tenant_id) as session:
            return await MembershipRepository().list_for_tenant(session, tenant_id)
