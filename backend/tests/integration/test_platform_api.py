"""platform 上下文接口的集成测试：认证、API Key、凭据、审计。"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from agent_platform.settings import get_settings
from agent_platform.shared.db import session_scope, system_session_scope
from agent_platform.shared.security import decrypt_secret
from tests.integration.conftest import TenantEnv

pytestmark = pytest.mark.usefixtures("tenant_env")


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_login_returns_token_and_identity(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str]
) -> None:
    me = await api_client.get("/api/v1/auth/me", headers=admin_headers)
    assert me.status_code == 200, me.text
    body = me.json()
    assert body["tenant_id"] == str(tenant_env.tenant_id)
    assert body["actor_id"] == str(tenant_env.user_id)
    assert "tenant_admin" in body["roles"]
    assert "agent:publish" in body["permissions"]


async def test_wrong_password_is_rejected_and_audited(
    api_client, tenant_env: TenantEnv
) -> None:
    response = await api_client.post(
        "/api/v1/auth/login",
        json={
            "tenant_code": tenant_env.tenant_code,
            "email": tenant_env.email,
            "password": "definitely-wrong",
        },
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"

    async with system_session_scope() as session:
        count = await session.scalar(
            text(
                "select count(*) from platform.audit_log "
                "where tenant_id = :tid and action = 'auth.login_failed' and result = 'FAILURE'"
            ),
            {"tid": tenant_env.tenant_id},
        )
    assert count >= 1


async def test_expired_token_is_rejected(api_client, tenant_env: TenantEnv) -> None:
    settings = get_settings()
    now = datetime.now(UTC) - timedelta(hours=2)
    token = jwt.encode(
        {
            "sub": str(tenant_env.user_id),
            "tid": str(tenant_env.tenant_id),
            "typ": "USER",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=1)).timestamp()),
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    response = await api_client.get("/api/v1/auth/me", headers=_bearer(token))
    assert response.status_code == 401


async def test_disabled_user_is_rejected(
    api_client, tenant_env: TenantEnv
) -> None:
    async with system_session_scope() as session:
        await session.execute(
            text("update platform.user_account set status = 'DISABLED' where id = :uid"),
            {"uid": tenant_env.user_id},
        )
    response = await api_client.post(
        "/api/v1/auth/login",
        json={
            "tenant_code": tenant_env.tenant_code,
            "email": tenant_env.email,
            "password": tenant_env.password,
        },
    )
    assert response.status_code == 401


async def test_api_key_plaintext_appears_once_and_disable_takes_effect(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str]
) -> None:
    created = await api_client.post(
        "/api/v1/api-keys",
        headers=admin_headers,
        json={"name": "ci", "scopes": ["run:read"]},
    )
    assert created.status_code == 201, created.text
    payload = created.json()
    plaintext = payload["secret"]
    assert plaintext.startswith("ap_")
    key_id = payload["id"]

    listed = await api_client.get("/api/v1/api-keys", headers=admin_headers)
    assert listed.status_code == 200
    assert "secret" not in json.dumps(listed.json())

    working = await api_client.get("/api/v1/auth/me", headers={"Authorization": f"ApiKey {plaintext}"})
    assert working.status_code == 200
    assert working.json()["actor_type"] == "API_KEY"

    disabled = await api_client.delete(f"/api/v1/api-keys/{key_id}", headers=admin_headers)
    assert disabled.status_code == 204

    rejected = await api_client.get("/api/v1/auth/me", headers={"Authorization": f"ApiKey {plaintext}"})
    assert rejected.status_code == 401


async def test_api_key_scope_outside_is_forbidden(
    api_client, admin_headers: dict[str, str]
) -> None:
    created = await api_client.post(
        "/api/v1/api-keys",
        headers=admin_headers,
        json={"name": "reader", "scopes": ["run:read"]},
    )
    plaintext = created.json()["secret"]

    response = await api_client.get(
        "/api/v1/audit-logs", headers={"X-API-Key": plaintext}
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


async def test_secret_is_masked_everywhere(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str]
) -> None:
    value = "sk-live-very-secret-value-0001"
    created = await api_client.post(
        "/api/v1/secrets", headers=admin_headers, json={"name": "openai", "value": value}
    )
    assert created.status_code == 201, created.text
    assert value not in created.text
    assert created.json()["masked"] == "********"

    listed = await api_client.get("/api/v1/secrets", headers=admin_headers)
    assert value not in listed.text

    rotated = await api_client.post(
        f"/api/v1/secrets/{created.json()['id']}/rotate",
        headers=admin_headers,
        json={"value": "sk-live-rotated-0002"},
    )
    assert rotated.status_code == 200
    assert rotated.json()["key_version"] == 2
    assert "sk-live-rotated-0002" not in rotated.text

    async with session_scope(tenant_env.tenant_id) as session:
        ciphertext = await session.scalar(
            text("select ciphertext from platform.secret_ref where name = 'openai'")
        )
    assert value not in ciphertext
    assert decrypt_secret(ciphertext) == "sk-live-rotated-0002"

    async with system_session_scope() as session:
        detail = await session.scalar(
            text(
                "select coalesce(string_agg(detail::text, ' '), '') from platform.audit_log "
                "where tenant_id = :tid and resource_type = 'secret'"
            ),
            {"tid": tenant_env.tenant_id},
        )
    assert value not in (detail or "")


async def test_audit_log_is_append_only_and_tenant_scoped(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str]
) -> None:
    await api_client.post(
        "/api/v1/api-keys", headers=admin_headers, json={"name": "audited", "scopes": []}
    )

    logs = await api_client.get("/api/v1/audit-logs", headers=admin_headers)
    assert logs.status_code == 200, logs.text
    body = logs.json()
    assert body["total"] >= 1
    assert {entry["tenant_id"] for entry in body["items"]} == {str(tenant_env.tenant_id)}
    assert any(entry["action"] == "api_key.created" for entry in body["items"])

    # 应用角色没有 audit_log 的 update/delete 权限
    with pytest.raises(DBAPIError):
        async with session_scope(tenant_env.tenant_id) as session:
            await session.execute(
                text("update platform.audit_log set action = 'tampered' where tenant_id = :tid"),
                {"tid": tenant_env.tenant_id},
            )

    # 没有更新/删除审计记录的路由
    assert (await api_client.delete("/api/v1/audit-logs", headers=admin_headers)).status_code in {
        404,
        405,
    }


async def test_audit_log_is_invisible_to_other_tenant(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str]
) -> None:
    from agent_platform.bootstrap import bootstrap
    from tests.integration.conftest import purge_tenant

    suffix = uuid.uuid4().hex[:8]
    other = await bootstrap(
        tenant_code=f"o{suffix}",
        tenant_name="Other Tenant",
        admin_email=f"other-{suffix}@example.com",
        admin_password="Passw0rd!456",
    )
    try:
        login = await api_client.post(
            "/api/v1/auth/login",
            json={
                "tenant_code": f"o{suffix}",
                "email": f"other-{suffix}@example.com",
                "password": "Passw0rd!456",
            },
        )
        other_headers = _bearer(login.json()["access_token"])
        logs = await api_client.get("/api/v1/audit-logs", headers=other_headers)
        assert logs.status_code == 200
        assert logs.json()["items"] == []
    finally:
        await purge_tenant(tenant_id=other.tenant_id, user_id=other.user_id)


async def test_member_without_audit_permission_is_forbidden(
    api_client, tenant_env: TenantEnv
) -> None:
    email = f"member-{uuid.uuid4().hex[:8]}@example.com"
    registered = await api_client.post(
        "/api/v1/auth/register",
        json={
            "tenant_code": tenant_env.tenant_code,
            "email": email,
            "password": "MemberPass123!",
            "display_name": "Member",
        },
    )
    assert registered.status_code == 201, registered.text

    login = await api_client.post(
        "/api/v1/auth/login",
        json={"tenant_code": tenant_env.tenant_code, "email": email, "password": "MemberPass123!"},
    )
    assert login.status_code == 200, login.text
    headers = _bearer(login.json()["access_token"])

    assert (await api_client.get("/api/v1/auth/me", headers=headers)).status_code == 200
    forbidden = await api_client.get("/api/v1/audit-logs", headers=headers)
    assert forbidden.status_code == 403

    async with system_session_scope() as session:
        await session.execute(
            text("delete from platform.membership where user_id = :uid"),
            {"uid": registered.json()["user_id"]},
        )
        await session.execute(
            text("delete from platform.user_role where user_id = :uid"),
            {"uid": registered.json()["user_id"]},
        )
        await session.execute(
            text("delete from platform.user_account where id = :uid"),
            {"uid": registered.json()["user_id"]},
        )
