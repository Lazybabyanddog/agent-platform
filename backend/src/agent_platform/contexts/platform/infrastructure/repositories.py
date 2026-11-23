"""platform 上下文的仓储。

所有方法都显式接收 tenant_id 或依赖调用方已注入的事务级租户上下文，
不存在"跨租户顺手查一下"的路径。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_platform.contexts.platform.domain.enums import (
    ApiKeyStatus,
    GrantSubjectType,
    MembershipStatus,
    SecretStatus,
    TenantStatus,
    UserStatus,
)
from agent_platform.contexts.platform.infrastructure.tables import (
    ApiKey,
    Membership,
    Permission,
    ResourceGrant,
    Role,
    RolePermission,
    SecretRef,
    Tenant,
    UserAccount,
    UserRole,
)


class TenantRepository:
    async def by_code(self, session: AsyncSession, code: str) -> Tenant | None:
        return await session.scalar(select(Tenant).where(Tenant.code == code))

    async def by_id(self, session: AsyncSession, tenant_id: uuid.UUID) -> Tenant | None:
        return await session.get(Tenant, tenant_id)

    async def list(self, session: AsyncSession, *, limit: int, offset: int) -> list[Tenant]:
        result = await session.scalars(
            select(Tenant).order_by(Tenant.created_at.desc()).limit(limit).offset(offset)
        )
        return list(result)

    async def create(
        self, session: AsyncSession, *, code: str, name: str, description: str | None = None
    ) -> Tenant:
        tenant = Tenant(code=code, name=name, description=description, status=TenantStatus.ACTIVE)
        session.add(tenant)
        await session.flush()
        return tenant


class UserRepository:
    async def by_email(self, session: AsyncSession, email: str) -> UserAccount | None:
        return await session.scalar(select(UserAccount).where(UserAccount.email == email.lower()))

    async def by_id(self, session: AsyncSession, user_id: uuid.UUID) -> UserAccount | None:
        return await session.get(UserAccount, user_id)

    async def create(
        self,
        session: AsyncSession,
        *,
        email: str,
        password_hash: str,
        display_name: str,
    ) -> UserAccount:
        user = UserAccount(
            email=email.lower(),
            password_hash=password_hash,
            display_name=display_name,
            status=UserStatus.ACTIVE,
        )
        session.add(user)
        await session.flush()
        return user


class MembershipRepository:
    async def get(
        self, session: AsyncSession, *, tenant_id: uuid.UUID, user_id: uuid.UUID
    ) -> Membership | None:
        return await session.scalar(
            select(Membership).where(
                Membership.tenant_id == tenant_id, Membership.user_id == user_id
            )
        )

    async def create(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        department: str | None = None,
    ) -> Membership:
        membership = Membership(
            tenant_id=tenant_id,
            user_id=user_id,
            department=department,
            status=MembershipStatus.ACTIVE,
        )
        session.add(membership)
        await session.flush()
        return membership

    async def list_for_tenant(self, session: AsyncSession, tenant_id: uuid.UUID) -> list[Membership]:
        result = await session.scalars(
            select(Membership).where(Membership.tenant_id == tenant_id).order_by(Membership.created_at)
        )
        return list(result)

    async def disable(self, session: AsyncSession, membership_id: uuid.UUID) -> None:
        membership = await session.get(Membership, membership_id)
        if membership is not None:
            membership.status = MembershipStatus.DISABLED


class AuthorizationRepository:
    async def role_ids_for_user(
        self, session: AsyncSession, *, tenant_id: uuid.UUID, user_id: uuid.UUID
    ) -> list[uuid.UUID]:
        result = await session.scalars(
            select(UserRole.role_id).where(
                UserRole.tenant_id == tenant_id, UserRole.user_id == user_id
            )
        )
        return list(result)

    async def role_codes_for_user(
        self, session: AsyncSession, *, tenant_id: uuid.UUID, user_id: uuid.UUID
    ) -> list[str]:
        stmt = (
            select(Role.code)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.tenant_id == tenant_id, UserRole.user_id == user_id)
        )
        result = await session.scalars(stmt)
        return list(result)

    async def permissions_for_user(
        self, session: AsyncSession, *, tenant_id: uuid.UUID, user_id: uuid.UUID
    ) -> set[str]:
        stmt = (
            select(Permission.code)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .join(Role, Role.id == RolePermission.role_id)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.tenant_id == tenant_id, UserRole.user_id == user_id)
        )
        result = await session.scalars(stmt)
        return set(result)

    async def assign_role(
        self, session: AsyncSession, *, tenant_id: uuid.UUID, user_id: uuid.UUID, role_id: uuid.UUID
    ) -> None:
        existing = await session.scalar(
            select(UserRole).where(
                UserRole.tenant_id == tenant_id,
                UserRole.user_id == user_id,
                UserRole.role_id == role_id,
            )
        )
        if existing is None:
            session.add(UserRole(tenant_id=tenant_id, user_id=user_id, role_id=role_id))
            await session.flush()

    async def role_by_code(
        self, session: AsyncSession, *, tenant_id: uuid.UUID, code: str
    ) -> Role | None:
        return await session.scalar(
            select(Role).where(
                Role.code == code, (Role.tenant_id == tenant_id) | (Role.tenant_id.is_(None))
            )
        )

    async def has_grant(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        resource_type: str,
        resource_id: uuid.UUID | None,
        user_id: uuid.UUID,
        role_ids: list[uuid.UUID],
        action: str,
    ) -> bool:
        stmt = select(ResourceGrant.id).where(
            ResourceGrant.tenant_id == tenant_id,
            ResourceGrant.resource_type == resource_type,
            ResourceGrant.actions.op("?")(action),
        )
        if resource_id is not None:
            stmt = stmt.where(ResourceGrant.resource_id == resource_id)

        subject_clauses = [
            (ResourceGrant.subject_type == GrantSubjectType.USER) & (ResourceGrant.subject_id == user_id),
            (ResourceGrant.subject_type == GrantSubjectType.TENANT) & (ResourceGrant.subject_id == tenant_id),
        ]
        if role_ids:
            subject_clauses.append(
                (ResourceGrant.subject_type == GrantSubjectType.ROLE)
                & (ResourceGrant.subject_id.in_(role_ids))
            )
        from sqlalchemy import or_

        stmt = stmt.where(or_(*subject_clauses)).limit(1)
        return await session.scalar(stmt) is not None

    async def create_grant(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        resource_type: str,
        resource_id: uuid.UUID,
        subject_type: GrantSubjectType,
        subject_id: uuid.UUID,
        actions: list[str],
        granted_by: uuid.UUID | None,
    ) -> ResourceGrant:
        grant = ResourceGrant(
            tenant_id=tenant_id,
            resource_type=resource_type,
            resource_id=resource_id,
            subject_type=subject_type,
            subject_id=subject_id,
            actions=actions,
            granted_by=granted_by,
        )
        session.add(grant)
        await session.flush()
        return grant

    async def delete_grant(self, session: AsyncSession, *, tenant_id: uuid.UUID, grant_id: uuid.UUID) -> int:
        result = await session.execute(
            delete(ResourceGrant).where(
                ResourceGrant.tenant_id == tenant_id, ResourceGrant.id == grant_id
            )
        )
        return int(result.rowcount or 0)

    async def list_grants(
        self, session: AsyncSession, *, tenant_id: uuid.UUID, resource_type: str | None = None
    ) -> list[ResourceGrant]:
        stmt = select(ResourceGrant).where(ResourceGrant.tenant_id == tenant_id)
        if resource_type:
            stmt = stmt.where(ResourceGrant.resource_type == resource_type)
        result = await session.scalars(stmt.order_by(ResourceGrant.created_at.desc()))
        return list(result)


class ApiKeyRepository:
    async def by_key_id(self, session: AsyncSession, key_id: str) -> ApiKey | None:
        return await session.scalar(select(ApiKey).where(ApiKey.key_id == key_id))

    async def create(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        key_id: str,
        secret_hash: str,
        name: str,
        scopes: list[str],
        expires_at: datetime | None = None,
    ) -> ApiKey:
        key = ApiKey(
            tenant_id=tenant_id,
            key_id=key_id,
            secret_hash=secret_hash,
            name=name,
            scopes=scopes,
            expires_at=expires_at,
            status=ApiKeyStatus.ACTIVE,
        )
        session.add(key)
        await session.flush()
        return key

    async def list_for_tenant(self, session: AsyncSession, tenant_id: uuid.UUID) -> list[ApiKey]:
        result = await session.scalars(
            select(ApiKey).where(ApiKey.tenant_id == tenant_id).order_by(ApiKey.created_at.desc())
        )
        return list(result)

    async def disable(self, session: AsyncSession, *, tenant_id: uuid.UUID, key: ApiKey) -> None:
        key.status = ApiKeyStatus.DISABLED

    async def touch(self, session: AsyncSession, key: ApiKey) -> None:
        key.last_used_at = datetime.now(UTC)


class SecretRepository:
    async def by_id(
        self, session: AsyncSession, *, tenant_id: uuid.UUID, secret_id: uuid.UUID
    ) -> SecretRef | None:
        return await session.scalar(
            select(SecretRef).where(SecretRef.tenant_id == tenant_id, SecretRef.id == secret_id)
        )

    async def by_name(self, session: AsyncSession, *, tenant_id: uuid.UUID, name: str) -> SecretRef | None:
        return await session.scalar(
            select(SecretRef).where(SecretRef.tenant_id == tenant_id, SecretRef.name == name)
        )

    async def list_for_tenant(self, session: AsyncSession, tenant_id: uuid.UUID) -> list[SecretRef]:
        result = await session.scalars(
            select(SecretRef).where(SecretRef.tenant_id == tenant_id).order_by(SecretRef.name)
        )
        return list(result)

    async def create(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        name: str,
        ciphertext: str,
        backend: str = "local-aes-gcm",
    ) -> SecretRef:
        ref = SecretRef(
            tenant_id=tenant_id,
            name=name,
            ciphertext=ciphertext,
            backend=backend,
            status=SecretStatus.ACTIVE,
            key_version=1,
        )
        session.add(ref)
        await session.flush()
        return ref
