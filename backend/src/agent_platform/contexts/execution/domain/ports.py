"""执行引擎依赖的外部能力端口。

应用层只依赖这些协议；厂商 SDK 只出现在 infrastructure 适配器里。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from agent_platform.contexts.execution.domain.enums import ModelErrorCode


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: str
    content: str
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None


@dataclass(frozen=True, slots=True)
class ToolBinding:
    code: str
    description: str | None
    input_schema: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ModelRequest:
    model_name: str
    params: dict[str, Any]
    messages: list[ChatMessage]
    tools: list[ToolBinding] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ModelResponse:
    content: str
    tool_calls: tuple[ToolCall, ...] = ()
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True, slots=True)
class ToolResult:
    ok: bool
    content: str
    error_code: str | None = None


class ModelPortError(Exception):
    def __init__(self, code: ModelErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ModelPort(Protocol):
    async def chat(self, request: ModelRequest) -> ModelResponse: ...


class ToolPort(Protocol):
    async def invoke(
        self, *, tool_code: str, tool_version_id: str, arguments: dict[str, Any]
    ) -> ToolResult: ...


@dataclass(frozen=True, slots=True)
class LLMCallRecord:
    run_id: uuid.UUID
    trace_id: str
    span_id: str
    model_id: uuid.UUID
    model_name: str
    params: dict[str, Any]
    messages: list[dict[str, Any]]
    tool_count: int
    response_content: str | None
    response_tool_calls: tuple[ToolCall, ...]
    input_tokens: int
    output_tokens: int
    latency_ms: int
    status: str
    error_code: str | None
    error_message: str | None
    started_at: datetime
    ended_at: datetime


@dataclass(frozen=True, slots=True)
class ToolCallRecord:
    run_id: uuid.UUID
    trace_id: str
    span_id: str
    tool_id: uuid.UUID | None
    tool_code: str
    tool_version_id: uuid.UUID
    input_schema: dict[str, Any] | None
    arguments: dict[str, Any]
    response_content: str | None
    ok: bool
    error_code: str | None
    error_message: str | None
    latency_ms: int
    started_at: datetime
    ended_at: datetime
    # MCP 工具记录远端工具名，便于跨平台对账；HTTP 工具为空
    remote_tool_name: str | None = None


class CallRecorder(Protocol):
    """落调用轨迹。实现方不得让记录失败影响执行。"""

    async def record_llm_call(self, record: LLMCallRecord) -> None: ...

    async def record_tool_call(self, record: ToolCallRecord) -> None: ...
