"""insight schema 的表定义：模型调用与工具调用轨迹。"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, Numeric, String, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from agent_platform.contexts.insight.domain.enums import CallStatus
from agent_platform.shared.base import Base, CreatedAtMixin, IdMixin

SCHEMA = "insight"


class LLMCall(Base, IdMixin, CreatedAtMixin):
    __tablename__ = "llm_call"
    __table_args__ = (
        Index("ix_llm_call_tenant_run_started", "tenant_id", "run_id", "started_at"),
        Index("ix_llm_call_tenant_trace", "tenant_id", "trace_id"),
        {"schema": SCHEMA},
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("execution.run.id", ondelete="CASCADE"),
        nullable=False,
    )
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    span_id: Mapped[str] = mapped_column(String(32), nullable=False)
    parent_span_id: Mapped[str | None] = mapped_column(String(32))
    model_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    model_name: Mapped[str] = mapped_column(String(200), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_estimate: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[CallStatus] = mapped_column(
        Enum(CallStatus, native_enum=False, length=16), nullable=False
    )
    error_code: Mapped[str | None] = mapped_column(String(64))
    tool_count: Mapped[int | None] = mapped_column(Integer)
    request_summary: Mapped[dict] = mapped_column(JSONB, nullable=False)
    response_summary: Mapped[dict] = mapped_column(JSONB, nullable=False)
    request_body: Mapped[dict | None] = mapped_column(JSONB)
    response_body: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ToolCall(Base, IdMixin, CreatedAtMixin):
    __tablename__ = "tool_call"
    __table_args__ = (
        Index("ix_tool_call_tenant_run_started", "tenant_id", "run_id", "started_at"),
        Index("ix_tool_call_tenant_trace", "tenant_id", "trace_id"),
        Index("ix_tool_call_tenant_tool_started", "tenant_id", "tool_id", "started_at"),
        {"schema": SCHEMA},
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("execution.run.id", ondelete="CASCADE"),
        nullable=False,
    )
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    span_id: Mapped[str] = mapped_column(String(32), nullable=False)
    parent_span_id: Mapped[str | None] = mapped_column(String(32))
    tool_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    tool_code: Mapped[str] = mapped_column(String(96), nullable=False)
    tool_version_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    response_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    request_summary: Mapped[dict] = mapped_column(JSONB, nullable=False)
    response_summary: Mapped[dict] = mapped_column(JSONB, nullable=False)
    request_body: Mapped[dict | None] = mapped_column(JSONB)
    response_body: Mapped[dict | None] = mapped_column(JSONB)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[CallStatus] = mapped_column(
        Enum(CallStatus, native_enum=False, length=16), nullable=False
    )
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


__all__ = ["LLMCall", "SCHEMA", "ToolCall"]
