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
    runtime_context: list[str] = Field(
        default_factory=list,
        description="要注入的运行时事实（now/timezone），与保存版本共用同一套校验",
    )
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
    title: str | None = Field(default=None, description="首轮输入摘要；正文留存关闭时为空")
    turn_count: int = Field(default=0, description="已产生运行的轮数")
    owned: bool = Field(default=True, description="是否由当前调用者创建；false 为历史遗留会话")
    can_delete: bool = Field(
        default=False,
        description="当前调用者能否删除该会话；无归属的存量会话仅租户管理员可删，不能与 owned 混用",
    )
    expired: bool = Field(default=False, description="是否已过期；过期会话历史只读")
    preview_config_hash: str | None = None


class SessionPageResponse(BaseModel):
    items: list[SessionItemResponse]
    total: int
    page: int
    page_size: int


class SessionHistoryMessageResponse(BaseModel):
    content: str | None = Field(default=None, description="正文留存关闭或无内容时为空")
    length: int = 0


class SessionHistoryToolResponse(BaseModel):
    name: str
    status: str
    latency_ms: int | None = None
    error: str | None = None


class SessionHistoryTurnResponse(BaseModel):
    index: int
    user: SessionHistoryMessageResponse
    assistant: SessionHistoryMessageResponse | None = None
    tools: list[SessionHistoryToolResponse] = Field(default_factory=list)
    run_id: uuid.UUID | None = None
    trace_id: str | None = None
    status: RunStatus | None = None
    error: dict[str, Any] | None = None
    started_at: datetime | None = None


class SessionHistoryResponse(BaseModel):
    session: SessionItemResponse
    turns: list[SessionHistoryTurnResponse]
    truncated: bool = False
    reconstructed: bool = Field(
        default=False,
        description="轮次由运行记录重建（线程中缺少用户消息锚点）；回答可能不完整",
    )
