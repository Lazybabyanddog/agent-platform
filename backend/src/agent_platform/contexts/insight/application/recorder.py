"""把执行期的模型/工具调用落成轨迹记录。

摘要与正文一并落库，供排障与复盘；工具参数与结果落库前必须先掩码。
记录失败只告警，不影响执行。
"""

from __future__ import annotations

import json
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
from agent_platform.shared.db import session_scope

_MAX_ERROR_MESSAGE = 1000


class InsightCallRecorder:
    def __init__(self, tenant_id: uuid.UUID) -> None:
        self._tenant_id = tenant_id

    async def record_llm_call(self, record: LLMCallRecord) -> None:
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
            "tool_count": record.tool_count,
            "request_summary": request_summary,
            "response_summary": response_summary,
            "started_at": record.started_at,
            "ended_at": record.ended_at,
            "request_body": {
                "model_name": record.model_name,
                "params": record.params,
                "messages": record.messages,
                "tool_count": record.tool_count,
                # 工具卡是请求的一部分：留全摘要才能复核"模型看到了什么"
                "tools": list(record.tools),
            },
            "response_body": record.response_content,
        }
        async with session_scope(self._tenant_id) as session:
            await LLMCallRepository().create(session, **values)

    async def record_tool_call(self, record: ToolCallRecord) -> None:
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
            "request_body": masked_arguments,
            "response_body": {"content": masked_response},
        }
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
