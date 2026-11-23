"""execution schema：运行记录、执行事件与 checkpoint 引用。"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from agent_platform.contexts.agent.domain.enums import Environment
from agent_platform.contexts.execution.domain.enums import CallerType, RunKind, RunStatus, SessionStatus
from agent_platform.shared.base import Base, CreatedAtMixin, IdMixin, TimestampMixin

SCHEMA = "execution"


class ExecutionSession(Base, IdMixin, TimestampMixin):
    """一次连续对话的上下文边界。

    正式会话首轮绑定发布记录与版本，预览会话首轮固化临时快照；两种会话的
    唯一键都包含 kind，因此预览与线上调用即使使用相同会话标识也不会互相串上下文。
    """

    __tablename__ = "execution_session"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "agent_id", "env", "kind", "session_key", name="uq_execution_session_scope"
        ),
        Index(
            "ix_execution_session_tenant_agent_active", "tenant_id", "agent_id", "last_active_at"
        ),
        Index("ix_execution_session_expires_at", "expires_at"),
        CheckConstraint(
            "(kind = 'INVOKE' AND agent_release_id IS NOT NULL AND agent_version_id IS NOT NULL"
            " AND preview_snapshot IS NULL)"
            " OR (kind = 'PREVIEW' AND agent_release_id IS NULL AND agent_version_id IS NULL"
            " AND preview_snapshot IS NOT NULL)",
            name="session_binding_matches_kind",
        ),
        {"schema": SCHEMA},
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("agent.agent.id", ondelete="CASCADE"), nullable=False
    )
    env: Mapped[Environment] = mapped_column(
        Enum(Environment, native_enum=False, length=16), nullable=False
    )
    session_key: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[RunKind] = mapped_column(
        Enum(RunKind, native_enum=False, length=16), nullable=False
    )
    agent_release_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("agent.agent_release.id", ondelete="RESTRICT")
    )
    agent_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("agent.agent_version.id", ondelete="RESTRICT")
    )
    preview_snapshot: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    status: Mapped[SessionStatus] = mapped_column(
        Enum(SessionStatus, native_enum=False, length=16),
        nullable=False,
        default=SessionStatus.IDLE,
        server_default=SessionStatus.IDLE.value,
    )
    busy_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_active_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Run(Base, IdMixin):
    __tablename__ = "run"
    __table_args__ = (
        Index("ix_run_tenant_agent_started", "tenant_id", "agent_id", "started_at"),
        Index("ix_run_tenant_agent_kind_started", "tenant_id", "agent_id", "kind", "started_at"),
        Index("ix_run_tenant_trace", "tenant_id", "trace_id"),
        CheckConstraint(
            "(kind = 'INVOKE' AND agent_release_id IS NOT NULL AND agent_version_id IS NOT NULL"
            " AND preview_snapshot IS NULL)"
            " OR (kind = 'PREVIEW' AND agent_release_id IS NULL AND agent_version_id IS NULL"
            " AND preview_snapshot IS NOT NULL)",
            name="run_binding_matches_kind",
        ),
        {"schema": SCHEMA},
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    kind: Mapped[RunKind] = mapped_column(
        Enum(RunKind, native_enum=False, length=16),
        nullable=False,
        default=RunKind.INVOKE,
        server_default=RunKind.INVOKE.value,
    )
    agent_release_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("agent.agent_release.id", ondelete="RESTRICT"),
    )
    agent_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("agent.agent_version.id", ondelete="RESTRICT"),
    )
    execution_session_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey(f"{SCHEMA}.execution_session.id", ondelete="SET NULL")
    )
    preview_snapshot: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    config_hash: Mapped[str | None] = mapped_column(String(64))
    env: Mapped[Environment] = mapped_column(
        Enum(Environment, native_enum=False, length=16), nullable=False
    )
    caller_type: Mapped[CallerType] = mapped_column(
        Enum(CallerType, native_enum=False, length=16), nullable=False
    )
    caller_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    session_id: Mapped[str | None] = mapped_column(String(128))
    input: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[RunStatus] = mapped_column(
        Enum(RunStatus, native_enum=False, length=16),
        nullable=False,
        default=RunStatus.RUNNING,
    )
    trace_id: Mapped[str] = mapped_column(String(36), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[dict | None] = mapped_column(JSONB)


class ExecutionEvent(Base, IdMixin, CreatedAtMixin):
    __tablename__ = "execution_event"
    __table_args__ = (
        UniqueConstraint("run_id", "seq", name="uq_execution_event_run_id_seq"),
        Index("ix_execution_event_tenant_run_seq", "tenant_id", "run_id", "seq"),
        {"schema": SCHEMA},
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey(f"{SCHEMA}.run.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class CheckpointRef(Base, IdMixin, CreatedAtMixin):
    """只存 LangGraph checkpoint 的定位信息，不复制其内部结构。"""

    __tablename__ = "checkpoint_ref"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_checkpoint_ref_run_id"),
        {"schema": SCHEMA},
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey(f"{SCHEMA}.run.id", ondelete="CASCADE"), nullable=False
    )
    thread_id: Mapped[str] = mapped_column(String(128), nullable=False)
    checkpoint_id: Mapped[str] = mapped_column(String(128), nullable=False)
