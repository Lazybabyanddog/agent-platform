"""agent 上下文的请求/响应模型。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agent_platform.contexts.agent.domain.enums import AgentStatus, AgentType, Environment
from agent_platform.contexts.platform.api.schemas import Page


class AgentConfigRequest(BaseModel):
    """只提交引用，资源内容由服务端装配时解析。"""

    model_id: uuid.UUID = Field(description="模型 id；服务端在装配时解析名称、上下文窗口与参数模板")
    prompt_version_id: uuid.UUID = Field(description="Prompt 版本 id；正文由服务端从版本快照读取")
    tool_version_ids: list[uuid.UUID] = Field(
        default_factory=list, description="工具版本 id 列表；按顺序绑定到版本"
    )
    params: dict[str, Any] = Field(
        default_factory=dict, description="模型参数覆盖值，按模型参数模板归一化"
    )
    policy: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "运行策略：max_steps、timeout_s、token_budget、session_ttl_h、history_token_limit；"
            "缺省时使用默认值"
        ),
    )
    runtime_context: list[str] = Field(
        default_factory=list,
        description=(
            "运行时上下文依赖声明：now（当前日期时间）、timezone（时区）；"
            "由服务端生成并注入，未声明的不会出现在 Prompt 中"
        ),
    )


class AgentCreateRequest(BaseModel):
    code: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    name: str = Field(min_length=1, max_length=160)
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    type: AgentType = AgentType.CHAT
    config: AgentConfigRequest | None = Field(
        default=None, description="可选初始配置；提供时在同一事务内生成版本 1"
    )


class AgentCopyRequest(BaseModel):
    code: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_.-]+$",
        description="新 Agent 编码，必须在本租户内唯一",
    )
    name: str | None = Field(default=None, max_length=160, description="缺省为「来源名称 (copy)」")


class AgentEnvironmentState(BaseModel):
    """某环境当前生效的发布快照；环境未发布时整个值为 null。"""

    agent_version_id: uuid.UUID
    version_no: int
    release_id: uuid.UUID
    released_at: datetime


class AgentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    description: str | None
    type: str
    status: AgentStatus
    owner_id: uuid.UUID | None
    tags: list[str]
    created_at: datetime
    updated_at: datetime
    environments: dict[str, AgentEnvironmentState | None] = Field(
        default_factory=dict,
        description="按 DEV/TEST/PROD 固定键给出当前生效版本，未发布为 null",
    )


class AgentListResponse(Page[AgentResponse]):
    page: int
    page_size: int


class AgentVersionCreateRequest(AgentConfigRequest):
    note: str | None = Field(default=None, max_length=500)
    base_version_id: uuid.UUID | None = Field(
        default=None,
        description="编辑所依据的版本；不是最新版本时返回 409 AGENT_VERSION_CONFLICT 与差异摘要",
    )


class AgentVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    agent_id: uuid.UUID
    version_no: int
    snapshot_schema_version: int
    snapshot: dict[str, Any]
    config_hash: str
    note: str | None
    created_by: uuid.UUID | None
    created_at: datetime


class AgentReleaseRequest(BaseModel):
    agent_version_id: uuid.UUID
    env: Environment
    note: str | None = Field(default=None, max_length=500)


class AgentRollbackRequest(BaseModel):
    env: Environment
    release_id: uuid.UUID


class AgentReleaseResponse(BaseModel):
    release_id: uuid.UUID
    agent_id: uuid.UUID
    agent_version_id: uuid.UUID
    version_no: int
    env: Environment
    status: str
    released_by: uuid.UUID | None
    released_at: datetime
    created_at: datetime


class AgentPointerResponse(BaseModel):
    agent_id: uuid.UUID
    env: Environment
    release_id: uuid.UUID
    agent_version_id: uuid.UUID
    version_no: int
    updated_at: datetime


class ComponentDiff(BaseModel):
    changed: bool
    base: dict[str, Any] | None = None
    target: dict[str, Any] | None = None


class ToolsDiff(BaseModel):
    added: list[dict[str, Any]] = Field(default_factory=list)
    removed: list[dict[str, Any]] = Field(default_factory=list)
    changed: list[dict[str, Any]] = Field(default_factory=list)


class RuntimeContextDiff(BaseModel):
    """运行时上下文只比较「需要哪些事实」，不比较事实值。"""

    changed: bool
    base: list[str] = Field(default_factory=list)
    target: list[str] = Field(default_factory=list)


class AgentVersionDiffResponse(BaseModel):
    base_version_no: int
    target_version_no: int
    identical: bool
    config_hash_changed: bool
    model: ComponentDiff
    prompt: ComponentDiff
    tools: ToolsDiff
    policy: dict[str, dict[str, Any]]
    runtime_context: RuntimeContextDiff


class BindingReadiness(BaseModel):
    role: str
    resource_type: str
    resource_id: uuid.UUID
    resource_version_id: uuid.UUID | None
    available: bool
    reason: str | None = None


class PreviewSummary(BaseModel):
    run_id: uuid.UUID
    status: str
    started_at: datetime
    finished_at: datetime | None
    trace_id: str | None


class AgentReadinessResponse(BaseModel):
    agent_id: uuid.UUID
    agent_version_id: uuid.UUID
    version_no: int
    config_hash: str
    ready: bool
    bindings: list[BindingReadiness]
    preview: PreviewSummary | None = None
