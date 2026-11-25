from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agent_platform.contexts.catalog.domain.enums import (
    McpProbeVerdict,
    ModelStatus,
    PromptStatus,
    ProviderStatus,
    RiskLevel,
    ToolStatus,
    ToolType,
)


class ProviderCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    base_url: str = Field(min_length=1, max_length=512)
    adapter: str = Field(default="openai_compatible", max_length=64)
    region: str | None = Field(default=None, max_length=32)
    secret_id: uuid.UUID | None = None


class ProviderUpdateRequest(BaseModel):
    """供应商可变更字段；未出现的字段保持不变。

    ``base_url`` 与 ``secret_id`` 会立即影响全部已发布版本，接口响应通过
    ``impact_notice`` 声明该影响范围。
    """

    name: str | None = Field(default=None, min_length=1, max_length=120)
    base_url: str | None = Field(default=None, min_length=1, max_length=512)
    adapter: str | None = Field(default=None, max_length=64)
    region: str | None = Field(default=None, max_length=32)
    secret_id: uuid.UUID | None = None
    status: ProviderStatus | None = None


class ProbeSummary(BaseModel):
    """列表里并排展示的最新一次探测摘要。"""

    verdict: str
    created_at: datetime
    observed_model_name: str | None = None


class ProviderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    adapter: str
    base_url: str
    region: str | None = None
    secret_ref_id: uuid.UUID | None
    status: str
    latest_probe: ProbeSummary | None = None
    # 仅在变更 Base URL 或凭据引用时返回，声明其对已发布版本的影响
    impact_notice: str | None = None


class ModelCreateRequest(BaseModel):
    provider_id: uuid.UUID
    code: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    model_name: str = Field(min_length=1, max_length=160)
    ctx_window: int | None = Field(default=None, ge=1)
    modalities: list[str] = Field(default_factory=lambda: ["text"])
    capabilities: dict[str, Any] = Field(default_factory=dict)
    param_template: dict[str, Any] = Field(default_factory=dict)
    price: dict[str, Any] = Field(default_factory=dict)


class ModelUpdateRequest(BaseModel):
    """模型可变更字段；``code`` 与 ``provider_id`` 出现即拒绝。"""

    model_name: str | None = Field(default=None, min_length=1, max_length=160)
    ctx_window: int | None = Field(default=None, ge=1)
    modalities: list[str] | None = None
    capabilities: dict[str, Any] | None = None
    param_template: dict[str, Any] | None = None
    price: dict[str, Any] | None = None
    # 仅为给出可定位到字段的拒绝原因而声明
    code: str | None = None
    provider_id: uuid.UUID | None = None


class ModelResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    provider_id: uuid.UUID
    code: str
    model_name: str
    ctx_window: int | None
    modalities: list[str]
    capabilities: dict[str, Any]
    param_template: dict[str, Any]
    price: dict[str, Any]
    status: str


class ModelStatusRequest(BaseModel):
    status: ModelStatus


class ParamsValidateRequest(BaseModel):
    params: dict[str, Any] = Field(default_factory=dict)


class ParamsValidateResponse(BaseModel):
    params: dict[str, Any]


class ProbeResponse(BaseModel):
    ok: bool
    verdict: str
    status_code: int | None
    latency_ms: int
    model_count: int | None
    error_code: str | None
    observed_models: list[str] | None = None
    observed_model_name: str | None = None
    probe_id: uuid.UUID
    created_at: datetime


class ProbeRecordResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    verdict: str
    status_code: int | None
    latency_ms: int
    observed_models: list[str] | None = None
    observed_model_name: str | None = None
    error_code: str | None
    actor_id: uuid.UUID | None
    created_at: datetime


class PromptCreateRequest(BaseModel):
    code: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    name: str = Field(min_length=1, max_length=160)
    description: str | None = None
    messages: list[dict[str, Any]] = Field(default_factory=list)
    variables: list[dict[str, Any]] = Field(default_factory=list)
    note: str | None = Field(default=None, max_length=500)


class PromptResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    description: str | None
    status: PromptStatus


class PromptVersionCreateRequest(BaseModel):
    messages: list[dict[str, Any]]
    variables: list[dict[str, Any]] = Field(default_factory=list)
    note: str | None = Field(default=None, max_length=500)


class PromptVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    prompt_id: uuid.UUID
    version_no: int
    messages: list[dict[str, Any]]
    variables: list[dict[str, Any]]
    config_hash: str
    note: str | None
    created_at: datetime


class PromptRenderRequest(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)


class PromptRenderResponse(BaseModel):
    messages: list[dict[str, str]]


class PromptDiffResponse(BaseModel):
    base_version_no: int
    target_version_no: int
    identical: bool
    message_changes: list[dict[str, Any]]
    variables_added: list[str]
    variables_removed: list[str]
    variables_changed: list[dict[str, Any]]


class ToolCreateRequest(BaseModel):
    code: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    name: str = Field(min_length=1, max_length=160)
    description: str | None = None
    type: ToolType = ToolType.HTTP
    spec: dict[str, Any]
    risk_level: RiskLevel = RiskLevel.READ_ONLY


class ToolResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    description: str | None
    type: ToolType
    status: ToolStatus
    risk_level: RiskLevel | None
    latest_version_no: int | None
    granted: bool
    updated_at: datetime


class ToolStatusRequest(BaseModel):
    status: ToolStatus


class ToolGrantResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    granted: bool
    granted_at: datetime | None
    granted_by: uuid.UUID | None


class ToolVersionCreateRequest(BaseModel):
    spec: dict[str, Any]
    risk_level: RiskLevel = RiskLevel.READ_ONLY


class ToolVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tool_id: uuid.UUID
    version_no: int
    spec: dict[str, Any]
    risk_level: RiskLevel
    config_hash: str
    # 只在保存版本时返回：不阻塞保存的契约提示（例如日期参数缺 format）
    warnings: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime


class ToolExecuteRequest(BaseModel):
    arguments: dict[str, Any] = Field(default_factory=dict)
    allow_high_risk: bool = False


class ToolExecuteResponse(BaseModel):
    status: str
    output: Any
    latency_ms: int
    request_digest: str
    response_digest: str | None
    error_code: str | None


class McpProbeRequest(BaseModel):
    """探测入参：只有「连到哪里、怎么连」，没有 tool_name——那正是探测的产出。"""

    transport: str = Field(default="streamable_http", max_length=32)
    url: str = Field(min_length=1, max_length=1024)
    auth: dict[str, Any] = Field(default_factory=lambda: {"type": "none"})
    headers: dict[str, Any] = Field(default_factory=dict)
    timeout_s: float = 15.0


class McpProbeToolResponse(BaseModel):
    name: str
    description: str | None
    input_schema: dict[str, Any] | None


class McpProbeResponse(BaseModel):
    verdict: McpProbeVerdict
    tools: list[McpProbeToolResponse]
    latency_ms: int
    error_summary: str | None
