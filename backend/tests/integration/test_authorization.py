"""RBAC 权限点 + ResourceGrant 双判定。"""

from __future__ import annotations

import uuid

import pytest

from agent_platform.contexts.platform.application.auth_service import auth_service
from agent_platform.contexts.platform.application.authorization_service import AuthorizationService
from agent_platform.contexts.platform.application.grant_service import grant_service
from agent_platform.contexts.platform.domain.enums import GrantSubjectType
from agent_platform.contexts.platform.infrastructure.repositories import UserRepository
from agent_platform.shared.context import ActorContext
from agent_platform.shared.db import session_scope, system_session_scope
from agent_platform.shared.errors import ForbiddenError
from agent_platform.shared.security import hash_password
from tests.integration.conftest import TenantEnv


async def test_user_without_membership_cannot_build_actor(tenant_env: TenantEnv) -> None:
    async with system_session_scope() as session:
        user = await UserRepository().create(
            session,
            email=f"outsider-{uuid.uuid4().hex[:8]}@example.com",
            password_hash=hash_password("OutsiderPass123!"),
            display_name="Outsider",
        )
        user_id = user.id
    try:
        with pytest.raises(ForbiddenError):
            await auth_service.build_actor(tenant_id=tenant_env.tenant_id, user_id=user_id)
    finally:
        async with system_session_scope() as session:
            from sqlalchemy import text

            await session.execute(
                text("delete from platform.user_account where id = :uid"), {"uid": user_id}
            )


async def test_permission_without_resource_grant_is_rejected(tenant_env: TenantEnv) -> None:
    actor = ActorContext(
        tenant_id=tenant_env.tenant_id,
        actor_type="USER",
        actor_id=uuid.uuid4(),
        permissions=frozenset({"agent:read"}),
        roles=frozenset({"member"}),
    )
    resource_id = uuid.uuid4()

    async with session_scope(tenant_env.tenant_id) as session:
        service = AuthorizationService(session)
        # 有权限点、没有资源授权 -> 拒绝
        with pytest.raises(ForbiddenError):
            await service.require_resource(
                actor,
                permission="agent:read",
                resource_type="agent",
                resource_id=resource_id,
                action="read",
            )

    grant = await grant_service.create(
        actor=actor,
        resource_type="agent",
        resource_id=resource_id,
        subject_type=GrantSubjectType.USER,
        subject_id=actor.actor_id,
        actions=["read"],
    )

    async with session_scope(tenant_env.tenant_id) as session:
        service = AuthorizationService(session)
        # 授权后通过
        await service.require_resource(
            actor,
            permission="agent:read",
            resource_type="agent",
            resource_id=resource_id,
            action="read",
        )
        # 未授权的动作仍被拒
        with pytest.raises(ForbiddenError):
            await service.require_resource(
                actor,
                permission="agent:read",
                resource_type="agent",
                resource_id=resource_id,
                action="publish",
            )

    await grant_service.revoke(actor=actor, grant_id=grant.id)

    async with session_scope(tenant_env.tenant_id) as session:
        with pytest.raises(ForbiddenError):
            await AuthorizationService(session).require_resource(
                actor,
                permission="agent:read",
                resource_type="agent",
                resource_id=resource_id,
                action="read",
            )


async def test_admin_bypasses_resource_grant(tenant_env: TenantEnv) -> None:
    admin = ActorContext(
        tenant_id=tenant_env.tenant_id,
        actor_type="USER",
        actor_id=uuid.uuid4(),
        permissions=frozenset({"agent:manage"}),
        roles=frozenset({"tenant_admin"}),
    )
    async with session_scope(tenant_env.tenant_id) as session:
        await AuthorizationService(session).require_resource(
            admin,
            permission="agent:manage",
            resource_type="agent",
            resource_id=uuid.uuid4(),
            action="manage",
        )


async def test_missing_permission_is_rejected(tenant_env: TenantEnv) -> None:
    member = ActorContext(
        tenant_id=tenant_env.tenant_id,
        actor_type="USER",
        actor_id=uuid.uuid4(),
        permissions=frozenset({"agent:read"}),
        roles=frozenset({"member"}),
    )
    with pytest.raises(ForbiddenError):
        await AuthorizationService().require_permission(member, "agent:manage")


async def test_member_cannot_toggle_tool_status_or_grant(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str]
) -> None:
    created = await api_client.post(
        "/api/v1/tools",
        headers=admin_headers,
        json={
            "code": f"rbac-{uuid.uuid4().hex[:6]}",
            "name": "RBAC",
            "spec": {"method": "POST", "url": "https://tools.local/echo"},
        },
    )
    assert created.status_code == 201, created.text
    tool_id = created.json()["id"]

    email = f"member-{uuid.uuid4().hex[:8]}@example.com"
    password = "MemberPass123!"
    registered = await api_client.post(
        "/api/v1/auth/register",
        json={
            "tenant_code": tenant_env.tenant_code,
            "email": email,
            "password": password,
            "display_name": "Member",
        },
    )
    assert registered.status_code == 201, registered.text
    try:
        login = await api_client.post(
            "/api/v1/auth/login",
            json={"tenant_code": tenant_env.tenant_code, "email": email, "password": password},
        )
        assert login.status_code == 200, login.text
        member_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        patched = await api_client.patch(
            f"/api/v1/tools/{tool_id}/status",
            headers=member_headers,
            json={"status": "DISABLED"},
        )
        assert patched.status_code == 403, patched.text
        granted = await api_client.post(
            f"/api/v1/tools/{tool_id}/grant", headers=member_headers
        )
        assert granted.status_code == 403, granted.text
        revoked = await api_client.delete(
            f"/api/v1/tools/{tool_id}/grant", headers=member_headers
        )
        assert revoked.status_code == 403, revoked.text

        unchanged = await api_client.get(f"/api/v1/tools/{tool_id}", headers=admin_headers)
        assert unchanged.json()["status"] == "ACTIVE"
        assert unchanged.json()["granted"] is False
    finally:
        from sqlalchemy import text

        async with system_session_scope() as session:
            user_id = registered.json()["user_id"]
            await session.execute(
                text("delete from platform.membership where user_id = :uid"), {"uid": user_id}
            )
            await session.execute(
                text("delete from platform.user_role where user_id = :uid"), {"uid": user_id}
            )
            await session.execute(
                text("delete from platform.user_account where id = :uid"), {"uid": user_id}
            )
