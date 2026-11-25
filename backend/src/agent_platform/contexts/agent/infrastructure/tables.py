"""agent schema 的表定义。"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from agent_platform.contexts.agent.domain.enums import (
    AgentStatus,
    AgentType,
    BindingResourceType,
    BindingRole,
    Environment,
    ReleaseStatus,
)
from agent_platform.shared.base import Base, CreatedAtMixin, IdMixin, TimestampMixin

SCHEMA = "agent"


class Agent(Base, IdMixin, TimestampMixin):
    __tablename__ = "agent"
    __table_args__ = (
        # 软删除后编码应立即释放，所以唯一性只约束未删除的行。
        Index(
            "uq_agent_tenant_id_code_active",
            "tenant_id",
            "code",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        {"schema": SCHEMA},
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    type: Mapped[AgentType] = mapped_column(
        Enum(AgentType, native_enum=False, length=32), nullable=False, default=AgentType.CHAT
    )
    owner_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    tags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[AgentStatus] = mapped_column(
        Enum(AgentStatus, native_enum=False, length=32), nullable=False, default=AgentStatus.DRAFT
    )
    # 软删除时间：非空即视为已删除，所有读取路径都会过滤掉它。
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )


class AgentVersion(Base, IdMixin, CreatedAtMixin):
    """只读快照。写入后不再修改，执行链只认这里的行。"""

    __tablename__ = "agent_version"
    __table_args__ = (
        UniqueConstraint("agent_id", "version_no", name="uq_agent_version_agent_id_version_no"),
        {"schema": SCHEMA},
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey(f"{SCHEMA}.agent.id", ondelete="CASCADE"), nullable=False
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    note: Mapped[str | None] = mapped_column(String(500))
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))


class AgentVersionBinding(Base, IdMixin, CreatedAtMixin):
    """版本引用了哪些具体资源版本，用于发布前复检与影响面分析。"""

    __tablename__ = "agent_version_binding"
    __table_args__ = (
        UniqueConstraint(
            "agent_version_id", "role", "resource_id", name="uq_agent_version_binding_role_resource"
        ),
        {"schema": SCHEMA},
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    agent_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(f"{SCHEMA}.agent_version.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[BindingRole] = mapped_column(
        Enum(BindingRole, native_enum=False, length=32), nullable=False
    )
    resource_type: Mapped[BindingResourceType] = mapped_column(
        Enum(BindingResourceType, native_enum=False, length=32), nullable=False
    )
    resource_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    resource_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    overrides: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class AgentRelease(Base, IdMixin, CreatedAtMixin):
    """发布记录只追加：同一版本重复发布也会产生新纪录，历史不被覆盖。"""

    __tablename__ = "agent_release"
    __table_args__ = (
        Index(
            "ix_agent_release_tenant_agent_env_released",
            "tenant_id",
            "agent_id",
            "env",
            "released_at",
        ),
        {"schema": SCHEMA},
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey(f"{SCHEMA}.agent.id", ondelete="CASCADE"), nullable=False
    )
    agent_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(f"{SCHEMA}.agent_version.id", ondelete="RESTRICT"),
        nullable=False,
    )
    env: Mapped[Environment] = mapped_column(
        Enum(Environment, native_enum=False, length=32), nullable=False
    )
    status: Mapped[ReleaseStatus] = mapped_column(
        Enum(ReleaseStatus, native_enum=False, length=32),
        nullable=False,
        default=ReleaseStatus.RELEASED,
    )
    released_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    released_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AgentReleasePointer(Base, CreatedAtMixin):
    """每个 Agent + 环境只有一行，指向当前生效的发布。"""

    __tablename__ = "agent_release_pointer"
    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "agent_id", "env", name="pk_agent_release_pointer"),
        {"schema": SCHEMA},
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey(f"{SCHEMA}.agent.id", ondelete="CASCADE"), nullable=False
    )
    env: Mapped[Environment] = mapped_column(
        Enum(Environment, native_enum=False, length=32), nullable=False
    )
    release_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(f"{SCHEMA}.agent_release.id", ondelete="RESTRICT"),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
