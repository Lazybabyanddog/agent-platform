"""租户生命周期与成员管理。"""

from __future__ import annotations

import uuid

from sqlalchemy import text

from agent_platform.shared.db import system_session_scope
from tests.integration.conftest import TenantEnv, purge_tenant


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_create_tenant_and_reject_duplicate_code(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str]
) -> None:
    code = f"new-{uuid.uuid4().hex[:8]}"
    created = await api_client.post(
        "/api/v1/tenants", headers=admin_headers, json={"code": code, "name": "New Tenant"}
    )
    assert created.status_code == 201, created.text
    tenant_id = created.json()["id"]
    try:
        assert created.json()["status"] == "ACTIVE"

        duplicated = await api_client.post(
            "/api/v1/tenants", headers=admin_headers, json={"code": code, "name": "Dup"}
        )
        assert duplicated.status_code == 409

        fetched = await api_client.get(f"/api/v1/tenants/{tenant_id}", headers=admin_headers)
        assert fetched.status_code == 200
    finally:
        await purge_tenant(tenant_id=uuid.UUID(tenant_id))


async def test_suspended_tenant_blocks_login_and_existing_token(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str]
) -> None:
    login = await api_client.post(
        "/api/v1/auth/login",
        json={
            "tenant_code": tenant_env.tenant_code,
            "email": tenant_env.email,
            "password": tenant_env.password,
        },
    )
    token = login.json()["access_token"]

    suspended = await api_client.patch(
        f"/api/v1/tenants/{tenant_env.tenant_id}/status",
        headers=admin_headers,
        json={"status": "SUSPENDED"},
    )
    assert suspended.status_code == 200

    blocked_login = await api_client.post(
        "/api/v1/auth/login",
        json={
            "tenant_code": tenant_env.tenant_code,
            "email": tenant_env.email,
            "password": tenant_env.password,
        },
    )
    assert blocked_login.status_code == 403

    blocked_token = await api_client.get("/api/v1/auth/me", headers=_bearer(token))
    assert blocked_token.status_code == 403

    async with system_session_scope() as session:
        await session.execute(
            text("update platform.tenant set status = 'ACTIVE' where id = :tid"),
            {"tid": tenant_env.tenant_id},
        )


async def test_disabled_member_loses_access(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str]
) -> None:
    suffix = uuid.uuid4().hex[:8]
    email = f"worker-{suffix}@example.com"
    registered = await api_client.post(
        "/api/v1/auth/register",
        json={
            "tenant_code": tenant_env.tenant_code,
            "email": email,
            "password": "WorkerPass123!",
            "display_name": "Worker",
        },
    )
    assert registered.status_code == 201, registered.text
    user_id = uuid.UUID(registered.json()["user_id"])

    members = await api_client.get("/api/v1/members", headers=admin_headers)
    assert members.status_code == 200
    membership = next(
        item for item in members.json()["items"] if item["user_id"] == str(user_id)
    )

    disabled = await api_client.delete(
        f"/api/v1/members/{membership['id']}", headers=admin_headers
    )
    assert disabled.status_code == 204

    blocked = await api_client.post(
        "/api/v1/auth/login",
        json={"tenant_code": tenant_env.tenant_code, "email": email, "password": "WorkerPass123!"},
    )
    assert blocked.status_code == 403

    async with system_session_scope() as session:
        await session.execute(
            text("delete from platform.membership where user_id = :uid"), {"uid": user_id}
        )
        await session.execute(
            text("delete from platform.user_role where user_id = :uid"), {"uid": user_id}
        )
        await session.execute(
            text("delete from platform.user_account where id = :uid"), {"uid": user_id}
        )
