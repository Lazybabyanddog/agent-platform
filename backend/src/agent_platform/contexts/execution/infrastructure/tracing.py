"""调用轨迹装饰器：把模型/工具调用交给 CallRecorder。

记录失败只告警，不影响本次执行结果；span_id 使用 OTel 风格的 16 位十六进制。
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from typing import Any

from agent_platform.contexts.execution.domain.enums import ModelErrorCode
from agent_platform.contexts.execution.domain.ports import (
    CallRecorder,
    ChatMessage,
    LLMCallRecord,
    ModelPort,
    ModelPortError,
    ModelRequest,
    ModelResponse,
    ToolCallRecord,
    ToolPort,
    ToolResult,
)
from agent_platform.shared.logging import get_logger

logger = get_logger(__name__)

SUCCESS = "SUCCESS"
FAILED = "FAILED"


def _span_id() -> str:
    return uuid.uuid4().hex[:16]


def _tool_payload(binding: Any) -> dict[str, Any]:
    """工具卡摘要：保留模型实际看到的名称、描述与参数契约。"""
    return {
        "name": binding.code,
        "description": binding.description,
        "parameters": binding.input_schema,
    }


def _message_payload(message: ChatMessage) -> dict[str, Any]:
    return {
        "role": message.role,
        "content": message.content,
        "tool_calls": [
            {"id": call.id, "name": call.name, "arguments": call.arguments}
            for call in message.tool_calls
        ],
        "tool_call_id": message.tool_call_id,
    }


class TracingModelPort:
    def __init__(
        self,
        inner: ModelPort,
        *,
        recorder: CallRecorder,
        run_id: uuid.UUID,
        trace_id: str,
        model_id: uuid.UUID,
        model_name: str,
    ) -> None:
        self._inner = inner
        self._recorder = recorder
        self._run_id = run_id
        self._trace_id = trace_id
        self._model_id = model_id
        self._model_name = model_name

    async def chat(self, request: ModelRequest) -> ModelResponse:
        span_id = _span_id()
        started_at = datetime.now(UTC)
        started = time.perf_counter()
        try:
            response = await self._inner.chat(request)
        except ModelPortError as exc:
            await self._record(
                request,
                span_id=span_id,
                started_at=started_at,
                started=started,
                status=FAILED,
                error_code=exc.code.value,
                error_message=exc.message,
            )
            raise
        except Exception as exc:  # noqa: BLE001 - 未知异常也要留下轨迹再抛出
            await self._record(
                request,
                span_id=span_id,
                started_at=started_at,
                started=started,
                status=FAILED,
                error_code=ModelErrorCode.UNKNOWN.value,
                error_message=str(exc),
            )
            raise
        await self._record(
            request,
            span_id=span_id,
            started_at=started_at,
            started=started,
            status=SUCCESS,
            response=response,
        )
        return response

    async def _record(
        self,
        request: ModelRequest,
        *,
        span_id: str,
        started_at: datetime,
        started: float,
        status: str,
        response: ModelResponse | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        record = LLMCallRecord(
            run_id=self._run_id,
            trace_id=self._trace_id,
            span_id=span_id,
            model_id=self._model_id,
            model_name=self._model_name,
            params=dict(request.params),
            messages=[_message_payload(item) for item in request.messages],
            tool_count=len(request.tools),
            response_content=response.content if response else None,
            response_tool_calls=response.tool_calls if response else (),
            input_tokens=response.input_tokens if response else 0,
            output_tokens=response.output_tokens if response else 0,
            latency_ms=int((time.perf_counter() - started) * 1000),
            status=status,
            error_code=error_code,
            error_message=error_message,
            started_at=started_at,
            ended_at=datetime.now(UTC),
            tools=tuple(_tool_payload(item) for item in request.tools),
        )
        logger.info(
            "llm.call",
            model_id=str(self._model_id),
            model_name=self._model_name,
            tool_count=record.tool_count,
            input_tokens=record.input_tokens,
            output_tokens=record.output_tokens,
            latency_ms=record.latency_ms,
            status=status,
            error_code=error_code,
        )
        try:
            await self._recorder.record_llm_call(record)
        except Exception:  # noqa: BLE001 - 轨迹是辅助能力
            logger.warning("trace.llm_write_failed", exc_info=True)


class TracingToolPort:
    def __init__(
        self,
        inner: ToolPort,
        *,
        recorder: CallRecorder,
        run_id: uuid.UUID,
        trace_id: str,
        tools: dict[str, tuple[uuid.UUID | None, dict[str, Any] | None, str | None]],
    ) -> None:
        self._inner = inner
        self._recorder = recorder
        self._run_id = run_id
        self._trace_id = trace_id
        self._tools = tools

    async def invoke(
        self, *, tool_code: str, tool_version_id: str, arguments: dict[str, Any]
    ) -> ToolResult:
        span_id = _span_id()
        started_at = datetime.now(UTC)
        started = time.perf_counter()
        tool_id, schema, remote_tool_name = self._tools.get(tool_version_id, (None, None, None))
        try:
            result = await self._inner.invoke(
                tool_code=tool_code, tool_version_id=tool_version_id, arguments=arguments
            )
        except Exception as exc:  # noqa: BLE001 - 失败也要留轨迹
            await self._record(
                span_id=span_id,
                started_at=started_at,
                started=started,
                tool_code=tool_code,
                tool_version_id=tool_version_id,
                tool_id=tool_id,
                schema=schema,
                remote_tool_name=remote_tool_name,
                arguments=arguments,
                result=None,
                error_code="TOOL_ERROR",
                error_message=str(exc),
            )
            raise
        await self._record(
            span_id=span_id,
            started_at=started_at,
            started=started,
            tool_code=tool_code,
            tool_version_id=tool_version_id,
            tool_id=tool_id,
            schema=schema,
            remote_tool_name=remote_tool_name,
            arguments=arguments,
            result=result,
            error_code=result.error_code,
            error_message=None if result.ok else result.content,
        )
        return result

    async def _record(
        self,
        *,
        span_id: str,
        started_at: datetime,
        started: float,
        tool_code: str,
        tool_version_id: str,
        tool_id: uuid.UUID | None,
        schema: dict[str, Any] | None,
        remote_tool_name: str | None,
        arguments: dict[str, Any],
        result: ToolResult | None,
        error_code: str | None,
        error_message: str | None,
    ) -> None:
        record = ToolCallRecord(
            run_id=self._run_id,
            trace_id=self._trace_id,
            span_id=span_id,
            tool_id=tool_id,
            tool_code=tool_code,
            tool_version_id=uuid.UUID(tool_version_id),
            input_schema=schema,
            arguments=arguments,
            response_content=result.content if result else None,
            ok=bool(result and result.ok),
            error_code=error_code,
            error_message=error_message,
            latency_ms=int((time.perf_counter() - started) * 1000),
            started_at=started_at,
            ended_at=datetime.now(UTC),
            remote_tool_name=remote_tool_name,
        )
        try:
            await self._recorder.record_tool_call(record)
        except Exception:  # noqa: BLE001 - 轨迹是辅助能力
            logger.warning("trace.tool_write_failed", exc_info=True)


def tool_index(
    snapshot: dict[str, Any],
) -> dict[str, tuple[uuid.UUID | None, dict[str, Any] | None, str | None]]:
    """tool_version_id → (tool_id, input_schema, 远端工具名)，供轨迹脱敏与对账使用。"""
    index: dict[str, tuple[uuid.UUID | None, dict[str, Any] | None, str | None]] = {}
    for item in snapshot.get("tools", []):
        spec = item.get("spec") or {}
        tool_id = item.get("tool_id")
        # 快照里只有 MCP 工具的 spec 带 tool_name（HTTP 契约不含该字段）
        remote_tool_name = spec.get("tool_name")
        index[str(item["tool_version_id"])] = (
            uuid.UUID(str(tool_id)) if tool_id else None,
            spec.get("input_schema"),
            remote_tool_name,
        )
    return index


__all__ = ["TracingModelPort", "TracingToolPort", "tool_index"]
