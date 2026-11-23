"""platform schema 的表定义。"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from agent_platform.contexts.platform.domain.enums import (
    ActorType,
    ApiKeyStatus,
    AuditResult,
    GrantSubjectType,
    MembershipStatus,
    OutboxStatus,
    SecretStatus,
    TenantStatus,
    UserStatus,
)
from agent_platform.shared.base import Base, CreatedAtMixin, IdMixin, TimestampMixin

SCHEMA = "platform"


class Tenant(Base, IdMixin, TimestampMixin):
    __tablename__ = "tenant"
    __table_args__ = {"schema": SCHEMA}

    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[TenantStatus] = mapped_column(
        Enum(TenantStatus, native_enum=False, length=32), nullable=False, default=TenantStatus.ACTIVE
    )
    description: Mapped[str | None] = mapped_column(Text)
    # 正文留存需租户显式开启，默认只落摘要与长度
    content_retention_enabled: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default=text("false")
    )


class UserAccount(Base, IdMixin, TimestampMixin):
    __tablename__ = "user_account"
    __table_args__ = {"schema": SCHEMA}

    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[UserStatus] = mapped_column(
        Enum(UserStatus, native_enum=False, length=32), nullable=False, default=UserStatus.ACTIVE
    )


class Membership(Base, IdMixin, TimestampMixin):
    __tablename__ = "membership"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", name="uq_membership_tenant_id_user_id"),
        {"schema": SCHEMA},
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey(f"{SCHEMA}.tenant.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(f"{SCHEMA}.user_account.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    department: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[MembershipStatus] = mapped_column(
        Enum(MembershipStatus, native_enum=False, length=32),
        nullable=False,
        default=MembershipStatus.ACTIVE,
    )


class Role(Base, IdMixin, TimestampMixin):
    __tablename__ = "role"
    __table_args__ = (
        Index(
            "uq_role_tenant_code",
            "tenant_id",
            "code",
            unique=True,
            postgresql_where=text("tenant_id IS NOT NULL"),
        ),
        Index(
            "uq_role_platform_code",
            "code",
            unique=True,
            postgresql_where=text("tenant_id IS NULL"),
        ),
        {"schema": SCHEMA},
    )

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True, index=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    builtin: Mapped[bool] = mapped_column(nullable=False, default=False)


class Permission(Base, IdMixin):
    __tablename__ = "permission"
    __table_args__ = {"schema": SCHEMA}

    code: Mapped[str] = mapped_column(String(96), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)


class RolePermission(Base):
    __tablename__ = "role_permission"
    __table_args__ = {"schema": SCHEMA}

    role_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey(f"{SCHEMA}.role.id", ondelete="CASCADE"), primary_key=True
    )
    permission_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(f"{SCHEMA}.permission.id", ondelete="CASCADE"),
        primary_key=True,
    )


class UserRole(Base, IdMixin, CreatedAtMixin):
    __tablename__ = "user_role"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", "role_id", name="uq_user_role_tenant_id_user_id_role_id"),
        {"schema": SCHEMA},
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    role_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey(f"{SCHEMA}.role.id", ondelete="CASCADE"), nullable=False
    )


class ResourceGrant(Base, IdMixin, TimestampMixin):
    __tablename__ = "resource_grant"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "resource_type",
            "resource_id",
            "subject_type",
            "subject_id",
            name="uq_resource_grant_tenant_resource_subject",
        ),
        {"schema": SCHEMA},
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    subject_type: Mapped[GrantSubjectType] = mapped_column(
        Enum(GrantSubjectType, native_enum=False, length=32), nullable=False
    )
    subject_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    actions: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    granted_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))


class ApiKey(Base, IdMixin, TimestampMixin):
    __tablename__ = "api_key"
    __table_args__ = (
        UniqueConstraint("key_id", name="uq_api_key_key_id"),
        CheckConstraint("length(key_id) >= 8", name="key_id_min_len"),
        {"schema": SCHEMA},
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey(f"{SCHEMA}.tenant.id", ondelete="CASCADE"), nullable=False, index=True
    )
    key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    secret_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    scopes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[ApiKeyStatus] = mapped_column(
        Enum(ApiKeyStatus, native_enum=False, length=32), nullable=False, default=ApiKeyStatus.ACTIVE
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SecretRef(Base, IdMixin, TimestampMixin):
    __tablename__ = "secret_ref"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_secret_ref_tenant_id_name"),
        {"schema": SCHEMA},
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    backend: Mapped[str] = mapped_column(String(32), nullable=False, default="local-aes-gcm")
    ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    key_version: Mapped[int] = mapped_column(nullable=False, default=1)
    status: Mapped[SecretStatus] = mapped_column(
        Enum(SecretStatus, native_enum=False, length=32), nullable=False, default=SecretStatus.ACTIVE
    )


class AuditLog(Base, IdMixin, CreatedAtMixin):
    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_tenant_created", "tenant_id", "created_at"),
        Index("ix_audit_log_resource", "tenant_id", "resource_type", "resource_id"),
        {"schema": SCHEMA},
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    actor_type: Mapped[ActorType] = mapped_column(
        Enum(ActorType, native_enum=False, length=32), nullable=False
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    action: Mapped[str] = mapped_column(String(96), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    result: Mapped[AuditResult] = mapped_column(
        Enum(AuditResult, native_enum=False, length=32), nullable=False
    )
    detail: Mapped[dict | None] = mapped_column(JSONB)
    trace_id: Mapped[str | None] = mapped_column(String(128))


class OutboxEvent(Base, IdMixin, CreatedAtMixin):
    __tablename__ = "outbox_event"
    __table_args__ = (
        Index("ix_outbox_event_status_next_attempt", "status", "next_attempt_at"),
        {"schema": SCHEMA},
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String(96), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[OutboxStatus] = mapped_column(
        Enum(OutboxStatus, native_enum=False, length=32), nullable=False, default=OutboxStatus.PENDING
    )
    attempts: Mapped[int] = mapped_column(nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
