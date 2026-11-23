"""catalog schema 的表定义。"""

from __future__ import annotations

import uuid

from sqlalchemy import Enum, ForeignKey, Index, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from agent_platform.contexts.catalog.domain.enums import (
    ModelStatus,
    ProbeVerdict,
    PromptStatus,
    ProviderStatus,
    RiskLevel,
    ToolStatus,
    ToolType,
)
from agent_platform.shared.base import Base, CreatedAtMixin, IdMixin, TimestampMixin

SCHEMA = "catalog"


class ModelProvider(Base, IdMixin, TimestampMixin):
    """供应商。tenant_id 为 NULL 时表示平台级共享供应商。"""

    __tablename__ = "model_provider"
    __table_args__ = ({"schema": SCHEMA},)

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    adapter: Mapped[str] = mapped_column(String(64), nullable=False, default="openai_compatible")
    base_url: Mapped[str] = mapped_column(String(512), nullable=False)
    # 区域只用于展示与筛选，不参与路由；自由文本以免限制私有部署
    region: Mapped[str | None] = mapped_column(String(32))
    secret_ref_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    status: Mapped[ProviderStatus] = mapped_column(
        Enum(ProviderStatus, native_enum=False, length=32),
        nullable=False,
        default=ProviderStatus.ACTIVE,
    )


class Model(Base, IdMixin, TimestampMixin):
    __tablename__ = "model"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code", name="uq_model_tenant_id_code"),
        {"schema": SCHEMA},
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    provider_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey(f"{SCHEMA}.model_provider.id", ondelete="RESTRICT"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    model_name: Mapped[str] = mapped_column(String(160), nullable=False)
    ctx_window: Mapped[int | None] = mapped_column(Integer)
    modalities: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    capabilities: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    param_template: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    price: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[ModelStatus] = mapped_column(
        Enum(ModelStatus, native_enum=False, length=32), nullable=False, default=ModelStatus.ACTIVE
    )


class ModelProbe(Base, IdMixin, CreatedAtMixin):
    """连通性探测记录：供应商级探测的 model_id 为 NULL，与模型级共用一张表。"""

    __tablename__ = "model_probe"
    __table_args__ = (
        Index("ix_model_probe_tenant_created", "tenant_id", "created_at"),
        Index("ix_model_probe_tenant_model", "tenant_id", "model_id"),
        {"schema": SCHEMA},
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    provider_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(f"{SCHEMA}.model_provider.id", ondelete="CASCADE"),
        nullable=False,
    )
    model_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey(f"{SCHEMA}.model.id", ondelete="CASCADE")
    )
    verdict: Mapped[ProbeVerdict] = mapped_column(
        Enum(ProbeVerdict, native_enum=False, length=32), nullable=False
    )
    status_code: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    observed_models: Mapped[list[str] | None] = mapped_column(JSONB)
    observed_model_name: Mapped[str | None] = mapped_column(String(160))
    error_code: Mapped[str | None] = mapped_column(String(64))
    actor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))


class Prompt(Base, IdMixin, TimestampMixin):
    __tablename__ = "prompt"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code", name="uq_prompt_tenant_id_code"),
        {"schema": SCHEMA},
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[PromptStatus] = mapped_column(
        Enum(PromptStatus, native_enum=False, length=32), nullable=False, default=PromptStatus.ACTIVE
    )


class PromptVersion(Base, IdMixin, CreatedAtMixin):
    """版本一经写入不可修改；对比与快照都指向具体版本行。"""

    __tablename__ = "prompt_version"
    __table_args__ = (
        UniqueConstraint("prompt_id", "version_no", name="uq_prompt_version_prompt_id_version_no"),
        {"schema": SCHEMA},
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    prompt_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey(f"{SCHEMA}.prompt.id", ondelete="CASCADE"), nullable=False
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    messages: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    variables: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    note: Mapped[str | None] = mapped_column(String(500))
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))


class Tool(Base, IdMixin, TimestampMixin):
    __tablename__ = "tool"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code", name="uq_tool_tenant_id_code"),
        {"schema": SCHEMA},
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    type: Mapped[ToolType] = mapped_column(
        Enum(ToolType, native_enum=False, length=32), nullable=False, default=ToolType.HTTP
    )
    status: Mapped[ToolStatus] = mapped_column(
        Enum(ToolStatus, native_enum=False, length=32), nullable=False, default=ToolStatus.ACTIVE
    )


class ToolVersion(Base, IdMixin, CreatedAtMixin):
    __tablename__ = "tool_version"
    __table_args__ = (
        UniqueConstraint("tool_id", "version_no", name="uq_tool_version_tool_id_version_no"),
        {"schema": SCHEMA},
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    tool_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey(f"{SCHEMA}.tool.id", ondelete="CASCADE"), nullable=False
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    spec: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    risk_level: Mapped[RiskLevel] = mapped_column(
        Enum(RiskLevel, native_enum=False, length=32), nullable=False, default=RiskLevel.READ_ONLY
    )
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class ToolGrant(Base, IdMixin, CreatedAtMixin):
    """租户内对某个工具的调用授权；没有授权不允许被 Agent 引用。"""

    __tablename__ = "tool_grant"
    __table_args__ = (
        UniqueConstraint("tenant_id", "tool_id", name="uq_tool_grant_tenant_id_tool_id"),
        {"schema": SCHEMA},
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    tool_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey(f"{SCHEMA}.tool.id", ondelete="CASCADE"), nullable=False
    )
    granted_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
