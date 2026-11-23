"""认证：自建邮箱密码 + JWT，AuthPort 隔离以便后续替换为 OIDC。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol

from agent_platform.contexts.platform.application.audit_service import audit_service
from agent_platform.contexts.platform.domain.enums import MembershipStatus, TenantStatus, UserStatus
from agent_platform.contexts.platform.domain.permissions import ROLE_MEMBER
from agent_platform.contexts.platform.infrastructure.repositories import (
    AuthorizationRepository,
    MembershipRepository,
    TenantRepository,
    UserRepository,
)
from agent_platform.shared.context import ActorContext
from agent_platform.shared.db import session_scope, system_session_scope
from agent_platform.shared.errors import ConflictError, ForbiddenError, UnauthorizedError
from agent_platform.shared.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


@dataclass(frozen=True, slots=True)
class LoginResult:
    token: str
    expires_in: int
    actor: ActorContext
    display_name: str


class AuthPort(Protocol):
    async def login(self, *, tenant_code: str, email: str, password: str) -> LoginResult: ...

    async def authenticate_token(self, token: str) -> ActorContext: ...


class AuthService:
    """默认实现：租户内邮箱密码登录 + 无状态 JWT。"""

    async def register(
        self,
        *,
        tenant_code: str,
        email: str,
        password: str,
        display_name: str,
    ) -> uuid.UUID:
        async with system_session_scope() as session:
            tenant = await TenantRepository().by_code(session, tenant_code)
            if tenant is None:
                raise UnauthorizedError("unknown tenant")
            if tenant.status != TenantStatus.ACTIVE:
                raise ForbiddenError("tenant is suspended")
            users = UserRepository()
            if await users.by_email(session, email):
                raise ConflictError("email already registered")
            user = await users.create(
                session,
                email=email,
                password_hash=hash_password(password),
                display_name=display_name,
            )
            tenant_id, user_id = tenant.id, user.id

        async with session_scope(tenant_id) as session:
            memberships = MembershipRepository()
            membership = await memberships.create(
                session, tenant_id=tenant_id, user_id=user_id
            )
            authz = AuthorizationRepository()
            role = await authz.role_by_code(session, tenant_id=tenant_id, code=ROLE_MEMBER)
            if role is not None:
                await authz.assign_role(
                    session, tenant_id=tenant_id, user_id=user_id, role_id=role.id
                )
            await audit_service.record(
                session,
                tenant_id=tenant_id,
                action="user.registered",
                resource_type="membership",
                resource_id=membership.id,
                detail={"email": email},
            )
        return user_id

    async def login(self, *, tenant_code: str, email: str, password: str) -> LoginResult:
        async with system_session_scope() as session:
            tenant = await TenantRepository().by_code(session, tenant_code)
            if tenant is None:
                raise UnauthorizedError("invalid email or password")
            if tenant.status != TenantStatus.ACTIVE:
                raise ForbiddenError("tenant is suspended")
            tenant_id = tenant.id
            user = await UserRepository().by_email(session, email)

        if user is None or not verify_password(user.password_hash, password):
            raise UnauthorizedError("invalid email or password")
        if user.status != UserStatus.ACTIVE:
            raise UnauthorizedError("user is disabled")

        actor = await self._build_actor(tenant_id=tenant_id, user_id=user.id, user=user)
        token, expires_in = create_access_token(actor_id=user.id, tenant_id=tenant_id)
        return LoginResult(
            token=token, expires_in=expires_in, actor=actor, display_name=user.display_name
        )

    async def authenticate_token(self, token: str) -> ActorContext:
        payload = decode_access_token(token)
        try:
            tenant_id = uuid.UUID(str(payload["tid"]))
            user_id = uuid.UUID(str(payload["sub"]))
        except (KeyError, ValueError) as exc:
            raise UnauthorizedError("invalid token payload") from exc
        return await self.build_actor(tenant_id=tenant_id, user_id=user_id)

    async def build_actor(self, *, tenant_id: uuid.UUID, user_id: uuid.UUID) -> ActorContext:
        async with system_session_scope() as session:
            tenant = await TenantRepository().by_id(session, tenant_id)
            if tenant is None:
                raise UnauthorizedError("invalid tenant")
            if tenant.status != TenantStatus.ACTIVE:
                raise ForbiddenError("tenant is suspended")
        return await self._build_actor(tenant_id=tenant_id, user_id=user_id)

    async def _build_actor(
        self,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        user=None,
    ) -> ActorContext:
        async with session_scope(tenant_id) as session:
            if user is None:
                user = await UserRepository().by_id(session, user_id)
                if user is None:
                    raise UnauthorizedError("unknown user")
                if user.status != UserStatus.ACTIVE:
                    raise UnauthorizedError("user is disabled")

            membership = await MembershipRepository().get(
                session, tenant_id=tenant_id, user_id=user_id
            )
            if membership is None or membership.status != MembershipStatus.ACTIVE:
                raise ForbiddenError("not an active member of this tenant")

            authz = AuthorizationRepository()
            permissions = await authz.permissions_for_user(
                session, tenant_id=tenant_id, user_id=user_id
            )
            roles = await authz.role_codes_for_user(session, tenant_id=tenant_id, user_id=user_id)

        return ActorContext(
            tenant_id=tenant_id,
            actor_type="USER",
            actor_id=user_id,
            permissions=frozenset(permissions),
            roles=frozenset(roles),
            display_name=user.display_name,
        )


auth_service = AuthService()
