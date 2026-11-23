"""服务身份：API Key 的创建、校验与禁用。

明文只在创建响应中出现一次，库中只存 key_id 与 secret 的 SHA-256。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from agent_platform.contexts.platform.application.audit_service import audit_service
from agent_platform.contexts.platform.domain.enums import ApiKeyStatus
from agent_platform.contexts.platform.infrastructure.repositories import (
    ApiKeyRepository,
    TenantRepository,
)
from agent_platform.contexts.platform.infrastructure.tables import ApiKey
from agent_platform.shared.context import ActorContext
from agent_platform.shared.db import session_scope, system_session_scope
from agent_platform.shared.errors import NotFoundError, UnauthorizedError
from agent_platform.shared.security import (
    generate_api_key,
    hash_api_secret,
    parse_api_key,
    verify_api_secret,
)


class ApiKeyService:
    async def create(
        self,
        *,
        actor: ActorContext,
        name: str,
        scopes: list[str],
        expires_in_days: int | None = None,
    ) -> tuple[ApiKey, str]:
        key_id, secret, plaintext = generate_api_key()
        expires_at = (
            datetime.now(UTC) + timedelta(days=expires_in_days) if expires_in_days else None
        )
        async with session_scope(actor.tenant_id) as session:
            key = await ApiKeyRepository().create(
                session,
                tenant_id=actor.tenant_id,
                key_id=key_id,
                secret_hash=hash_api_secret(secret),
                name=name,
                scopes=scopes,
                expires_at=expires_at,
            )
            await audit_service.record(
                session,
                tenant_id=actor.tenant_id,
                action="api_key.created",
                resource_type="api_key",
                resource_id=key.id,
                actor=actor,
                detail={"name": name, "scopes": scopes},
            )
            return key, plaintext

    async def authenticate(self, plaintext: str) -> ActorContext:
        parsed = parse_api_key(plaintext)
        if parsed is None:
            raise UnauthorizedError("invalid api key")
        key_id, secret = parsed

        # key_id 全局唯一，解析阶段必须跨租户查询，因此使用平台级连接
        async with system_session_scope() as session:
            repo = ApiKeyRepository()
            key = await repo.by_key_id(session, key_id)
            if key is None or not verify_api_secret(key.secret_hash, secret):
                raise UnauthorizedError("invalid api key")
            if key.status != ApiKeyStatus.ACTIVE:
                raise UnauthorizedError("api key is disabled")
            if key.expires_at is not None and key.expires_at <= datetime.now(UTC):
                raise UnauthorizedError("api key expired")
            tenant = await TenantRepository().by_id(session, key.tenant_id)
            if tenant is None or tenant.status.value != "ACTIVE":
                raise UnauthorizedError("tenant unavailable")
            await repo.touch(session, key)
            tenant_id = key.tenant_id

        return ActorContext(
            tenant_id=tenant_id,
            actor_type="API_KEY",
            actor_id=uuid.uuid5(uuid.NAMESPACE_URL, key_id),
            permissions=frozenset(key.scopes or []),
            roles=frozenset(),
            display_name=f"api-key:{key.name}",
        )

    async def list(self, tenant_id: uuid.UUID) -> list[ApiKey]:
        async with session_scope(tenant_id) as session:
            return await ApiKeyRepository().list_for_tenant(session, tenant_id)

    async def disable(self, *, actor: ActorContext, key_id: uuid.UUID) -> None:
        async with session_scope(actor.tenant_id) as session:
            repo = ApiKeyRepository()
            key = await session.get(ApiKey, key_id)
            if key is None or key.tenant_id != actor.tenant_id:
                raise NotFoundError("api key not found")
            await repo.disable(session, tenant_id=actor.tenant_id, key=key)
            await audit_service.record(
                session,
                tenant_id=actor.tenant_id,
                action="api_key.disabled",
                resource_type="api_key",
                resource_id=key.id,
                actor=actor,
            )


api_key_service = ApiKeyService()
