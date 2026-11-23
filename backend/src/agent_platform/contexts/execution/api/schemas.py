"""execution 上下文的请求/响应模型。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from agent_platform.contexts.agent.domain.enums import Environment
from agent_platform.contexts.execution.domain.enums import RunKind, RunStatus, SessionStatus


class InvokeRequest(BaseModel):
    env: Environment
    input: dict[str, Any] = Field(default_factory=dict)
    session_id: str | None = Field(default=None, max_length=128)
    timeout_s: float | None = Field(default=None, gt=0)


class PreviewRequest(BaseModel):
    """调试预览只提交引用与本次输入，不产生版本。"""

    env: Environment = Environment.DEV
    model_id: uuid.UUID
    prompt_version_id: uuid.UUID
    tool_version_ids: list[uuid.UUID] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)
    policy: dict[str, Any] = Field(default_factory=dict)
    input: dict[str, Any] = Field(default_factory=dict)
    preview_session_id: str | None = Field(
        default=None, max_length=128, description="可选，用于串联同一调试会话的多轮消息"
    )
    timeout_s: float | None = Field(default=None, gt=0)


class UsageResponse(BaseModel):
    steps: int
    tokens: int


class InvokeResponse(BaseModel):
    run_id: uuid.UUID
    trace_id: str
    status: RunStatus
    output: str | None = None
    usage: UsageResponse
    error: dict[str, Any] | None = None


class PreviewResponse(BaseModel):
    run_id: uuid.UUID
    trace_id: str
    status: RunStatus
    output: str | None = None
    usage: UsageResponse
    error: dict[str, Any] | None = None
    config_hash: str
    preview_snapshot: dict[str, Any]


class SessionItemResponse(BaseModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    env: Environment
    kind: RunKind
    session_key: str
    agent_release_id: uuid.UUID | None = None
    agent_version_id: uuid.UUID | None = None
    version_no: int | None = None
    status: SessionStatus
    last_active_at: datetime
    expires_at: datetime
    created_at: datetime


class SessionPageResponse(BaseModel):
    items: list[SessionItemResponse]
    total: int
    page: int
    page_size: int
