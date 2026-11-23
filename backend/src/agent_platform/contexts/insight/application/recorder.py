"""把执行期的模型/工具调用落成轨迹记录。

默认只落摘要与长度；租户显式开启正文留存后才落正文，且敏感字段必须先掩码。
记录失败只告警，不影响执行。
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from agent_platform.contexts.execution.domain.ports import LLMCallRecord, ToolCallRecord
from agent_platform.contexts.insight.domain.content_policy import (
    digest_payload,
    mask_sensitive,
    summarize_arguments,
    summarize_messages,
    summarize_text,
)
from agent_platform.contexts.insight.domain.enums import CallStatus
from agent_platform.contexts.insight.infrastructure.repositories import (
    LLMCallRepository,
    ToolCallRepository,
)
from agent_platform.contexts.platform.application.tenant_service import TenantService
from agent_platform.shared.db import session_scope

logger = logging.getLogger(__name__)

_MAX_ERROR_MESSAGE = 1000


class InsightCallRecorder:
    def __init__(self, tenant_id: uuid.UUID) -> None:
        self._tenant_id = tenant_id
        self._tenant_service = TenantService()
        self._retention: bool | None = None

    async def _content_retention_enabled(self) -> bool:
        if self._retention is None:
            try:
                self._retention = await self._tenant_service.content_retention_enabled(
                    self._tenant_id
                )
            except Exception:  # noqa: BLE001 - 配置读取失败按默认关闭处理
                logger.warning("failed to read content retention setting", exc_info=True)
                self._retention = False
        return self._retention

    async def record_llm_call(self, record: LLMCallRecord) -> None:
        keep_content = await self._content_retention_enabled()
        request_summary = summarize_messages(record.messages)
        request_summary.update({"model_name": record.model_name, "tool_count": record.tool_count})
        response_summary = summarize_text(record.response_content)
        response_summary.update(
            {
                "input_tokens": record.input_tokens,
                "output_tokens": record.output_tokens,
                "tool_call_count": len(record.response_tool_calls),
            }
        )
        values: dict[str, Any] = {
            "tenant_id": self._tenant_id,
            "run_id": record.run_id,
            "trace_id": record.trace_id,
            "span_id": record.span_id,
            "model_id": record.model_id,
            "model_name": record.model_name,
            "input_tokens": record.input_tokens,
            "output_tokens": record.output_tokens,
            "latency_ms": record.latency_ms,
            "status": CallStatus(record.status),
            "error_code": record.error_code,
            "request_summary": request_summary,
            "response_summary": response_summary,
            "started_at": record.started_at,
            "ended_at": record.ended_at,
        }
        if keep_content:
            values["request_body"] = {
                "model_name": record.model_name,
                "params": record.params,
                "messages": record.messages,
                "tool_count": record.tool_count,
            }
            values["response_body"] = record.response_content
        async with session_scope(self._tenant_id) as session:
            await LLMCallRepository().create(session, **values)

    async def record_tool_call(self, record: ToolCallRecord) -> None:
        keep_content = await self._content_retention_enabled()
        masked_arguments = mask_sensitive(record.arguments, record.input_schema)
        masked_response = _mask_response(record.response_content)
        request_summary = summarize_arguments(record.arguments, record.input_schema)
        response_summary = summarize_text(record.response_content)
        response_summary["ok"] = record.ok
        if record.remote_tool_name:
            response_summary["remote_tool_name"] = record.remote_tool_name
        values: dict[str, Any] = {
            "tenant_id": self._tenant_id,
            "run_id": record.run_id,
            "trace_id": record.trace_id,
            "span_id": record.span_id,
            "tool_id": record.tool_id,
            "tool_code": record.tool_code,
            "tool_version_id": record.tool_version_id,
            "request_digest": digest_payload(masked_arguments),
            "response_digest": digest_payload(masked_response),
            "request_summary": request_summary,
            "response_summary": response_summary,
            "latency_ms": record.latency_ms,
            "status": CallStatus.SUCCESS if record.ok else CallStatus.FAILED,
            "error_code": record.error_code,
            "error_message": (
                record.error_message[:_MAX_ERROR_MESSAGE] if record.error_message else None
            ),
            "started_at": record.started_at,
            "ended_at": record.ended_at,
        }
        if keep_content:
            values["request_body"] = masked_arguments
            values["response_body"] = {"content": masked_response}
        async with session_scope(self._tenant_id) as session:
            await ToolCallRepository().create(session, **values)


def _mask_response(content: str | None) -> Any:
    if content is None:
        return None
    try:
        parsed = json.loads(content)
    except (TypeError, ValueError):
        return content
    return mask_sensitive(parsed)


__all__ = ["InsightCallRecorder"]
