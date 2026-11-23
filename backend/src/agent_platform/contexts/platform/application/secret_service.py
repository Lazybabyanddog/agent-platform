"""凭据引用管理：内容加密落库，接口只返回引用与状态。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from agent_platform.contexts.platform.application.audit_service import audit_service
from agent_platform.contexts.platform.domain.enums import SecretStatus
from agent_platform.contexts.platform.infrastructure.repositories import SecretRepository
from agent_platform.shared.context import ActorContext
from agent_platform.shared.db import session_scope
from agent_platform.shared.errors import NotFoundError, ResourceUnavailableError
from agent_platform.shared.security import decrypt_secret, encrypt_secret


@dataclass(frozen=True, slots=True)
class SecretView:
    id: uuid.UUID
    name: str
    backend: str
    status: str
    key_version: int
    created_at: datetime
    masked: str = "********"


class SecretService:
    async def create(self, *, actor: ActorContext, name: str, value: str) -> SecretView:
        async with session_scope(actor.tenant_id) as session:
            repo = SecretRepository()
            existing = await repo.by_name(session, tenant_id=actor.tenant_id, name=name)
            if existing is not None:
                # 同名视为轮换：引用标识不变，内容与版本更新
                existing.ciphertext = encrypt_secret(value)
                existing.key_version += 1
                existing.status = SecretStatus.ACTIVE
                await audit_service.record(
                    session,
                    tenant_id=actor.tenant_id,
                    action="secret.rotated",
                    resource_type="secret",
                    resource_id=existing.id,
                    actor=actor,
                    detail={"name": name, "key_version": existing.key_version},
                )
                return self._view(existing)

            ref = await repo.create(
                session, tenant_id=actor.tenant_id, name=name, ciphertext=encrypt_secret(value)
            )
            await audit_service.record(
                session,
                tenant_id=actor.tenant_id,
                action="secret.created",
                resource_type="secret",
                resource_id=ref.id,
                actor=actor,
                detail={"name": name},
            )
            return self._view(ref)

    async def rotate(
        self, *, actor: ActorContext, secret_id: uuid.UUID, value: str
    ) -> SecretView:
        async with session_scope(actor.tenant_id) as session:
            ref = await SecretRepository().by_id(
                session, tenant_id=actor.tenant_id, secret_id=secret_id
            )
            if ref is None:
                raise NotFoundError("secret not found")
            ref.ciphertext = encrypt_secret(value)
            ref.key_version += 1
            ref.status = SecretStatus.ACTIVE
            await audit_service.record(
                session,
                tenant_id=actor.tenant_id,
                action="secret.rotated",
                resource_type="secret",
                resource_id=ref.id,
                actor=actor,
                detail={"key_version": ref.key_version},
            )
            return self._view(ref)

    async def disable(self, *, actor: ActorContext, secret_id: uuid.UUID) -> None:
        async with session_scope(actor.tenant_id) as session:
            ref = await SecretRepository().by_id(
                session, tenant_id=actor.tenant_id, secret_id=secret_id
            )
            if ref is None:
                raise NotFoundError("secret not found")
            ref.status = SecretStatus.DISABLED
            await audit_service.record(
                session,
                tenant_id=actor.tenant_id,
                action="secret.disabled",
                resource_type="secret",
                resource_id=ref.id,
                actor=actor,
            )

    async def list(self, tenant_id: uuid.UUID) -> list[SecretView]:
        async with session_scope(tenant_id) as session:
            refs = await SecretRepository().list_for_tenant(session, tenant_id)
            return [self._view(ref) for ref in refs]

    async def resolve(self, *, tenant_id: uuid.UUID, secret_id: uuid.UUID) -> str:
        """只在调用外部服务前解密；结果不得进入日志或响应。"""
        async with session_scope(tenant_id) as session:
            ref = await SecretRepository().by_id(session, tenant_id=tenant_id, secret_id=secret_id)
            if ref is None:
                raise ResourceUnavailableError("secret not found")
            if ref.status != SecretStatus.ACTIVE:
                raise ResourceUnavailableError("secret is disabled")
            return decrypt_secret(ref.ciphertext)

    @staticmethod
    def _view(ref) -> SecretView:
        return SecretView(
            id=ref.id,
            name=ref.name,
            backend=ref.backend,
            status=ref.status.value if hasattr(ref.status, "value") else str(ref.status),
            key_version=ref.key_version,
            created_at=ref.created_at,
        )


secret_service = SecretService()
