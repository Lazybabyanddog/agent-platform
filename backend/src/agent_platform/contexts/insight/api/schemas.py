"""insight API 的请求/响应结构。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class TraceTokenUsage(BaseModel):
    input: int
    output: int


class TraceContent(BaseModel):
    request: Any | None = None
    response: Any | None = None


class TraceEntryResponse(BaseModel):
    type: str
    trace_id: str
    span_id: str
    label: str
    status: str
    error_code: str | None = None
    started_at: datetime
    ended_at: datetime
    latency_ms: int
    summary: dict[str, Any]
    tokens: TraceTokenUsage | None = None
    content: TraceContent | None = None
    is_slowest: bool = False


class TraceRunResponse(BaseModel):
    run_id: uuid.UUID
    trace_id: str
    agent_id: uuid.UUID
    env: str
    kind: str
    config_hash: str | None = None
    agent_release_id: uuid.UUID | None = None
    agent_version_id: uuid.UUID | None = None
    execution_session_id: uuid.UUID | None = None
    session_id: str | None = None
    status: str
    caller_type: str
    started_at: datetime
    finished_at: datetime | None = None
    error: dict[str, Any] | None = None


class RunTraceResponse(BaseModel):
    run: TraceRunResponse
    entries: list[TraceEntryResponse]
    slowest_span_id: str | None = None
    slowest_latency_ms: int | None = None


class RunSummaryItemResponse(BaseModel):
    run_id: uuid.UUID
    trace_id: str
    agent_id: uuid.UUID
    env: str
    kind: str
    status: str
    config_hash: str | None = None
    agent_release_id: uuid.UUID | None = None
    agent_version_id: uuid.UUID | None = None
    execution_session_id: uuid.UUID | None = None
    session_id: str | None = None
    started_at: datetime
    finished_at: datetime | None = None


class RunPageResponse(BaseModel):
    items: list[RunSummaryItemResponse]
    total: int
    page: int
    page_size: int


class RunMetricsResponse(BaseModel):
    agent_id: uuid.UUID | None = None
    window_start: datetime | None = None
    window_end: datetime | None = None
    kind: str | None = None
    include_preview: bool = False
    total: int
    succeeded: int
    failed: int
    sessions: int = 0
    success_rate: float
    failure_rate: float
    p50_latency_ms: float | None = None
    p95_latency_ms: float | None = None


class ToolCallItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tool_id: uuid.UUID | None = None
    tool_version_id: uuid.UUID
    status: str
    error_code: str | None = None
    latency_ms: int
    started_at: datetime


class ToolCallErrorCount(BaseModel):
    code: str
    count: int


class ToolCallOverviewResponse(BaseModel):
    tool_id: uuid.UUID
    window_start: datetime
    window_end: datetime
    total: int
    succeeded: int
    failed: int
    success_rate: float | None = None
    avg_latency_ms: float | None = None
    errors: list[ToolCallErrorCount]
    items: list[ToolCallItemResponse]
