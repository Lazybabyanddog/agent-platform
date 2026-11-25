"""把 catalog 的工具执行器（HTTP / MCP）适配成执行引擎的 ToolPort。"""

from __future__ import annotations

import json
import uuid
from typing import Any

from agent_platform.contexts.catalog.application.tool_service import tool_service
from agent_platform.contexts.catalog.domain.errors import (
    HighRiskToolError,
    ToolExecutionError,
    ToolMcpError,
    ToolOutputInvalidError,
    ToolTimeoutError,
)
from agent_platform.contexts.execution.domain.ports import ToolResult
from agent_platform.shared.context import ActorContext
from agent_platform.shared.errors import NotFoundError, ResourceUnavailableError, ValidationFailedError

# 白名单而非黑名单：新增错误码默认不可恢复，避免"可重试"被静默扩大
RECOVERABLE_ERROR_CODES = frozenset(
    {
        "INPUT_INVALID",
        "TOOL_TIMEOUT",
        "TOOL_HTTP_ERROR",
        "TOOL_MCP_ERROR",
        "RESOURCE_UNAVAILABLE",
    }
)


def _details(exc: Exception) -> tuple[dict[str, str], ...]:
    raw = getattr(exc, "details", None) or ()
    return tuple(
        {
            "field": str(item.get("field") or ""),
            "reason": str(item.get("reason") or ""),
        }
        for item in raw
        if isinstance(item, dict)
    )


def _describe(exc: Exception) -> str:
    """把字段级原因并入可读正文。

    ``ValidationFailedError`` 的 message 是固定文案，具体字段只在 details 里；
    模型只读正文，所以这里必须把 details 拼进去，否则回灌等于没说。
    其它错误的正文保持原样，避免改变既有展示与日志。
    """
    message = str(getattr(exc, "message", None) or exc)
    if not isinstance(exc, ValidationFailedError):
        return message
    parts = [
        f"{item['field']}: {item['reason']}"
        for item in _details(exc)
        if item["field"] or item["reason"]
    ]
    return f"{message} ({'; '.join(parts)})" if parts else message


def _failure(exc: Exception, code: str) -> ToolResult:
    return ToolResult(
        ok=False,
        content=_describe(exc),
        error_code=code,
        details=_details(exc),
        recoverable=code in RECOVERABLE_ERROR_CODES,
    )


class CatalogToolPort:
    """执行时再次做工具授权校验，并把异常转换成可记录的失败结果。"""

    def __init__(
        self,
        *,
        actor: ActorContext,
        allow_high_risk: bool = False,
        service: Any = None,
    ) -> None:
        self._actor = actor
        self._allow_high_risk = allow_high_risk
        self._service = service or tool_service

    async def invoke(
        self, *, tool_code: str, tool_version_id: str, arguments: dict[str, Any]
    ) -> ToolResult:
        try:
            result = await self._service.execute(
                actor=self._actor,
                tool_version_id=uuid.UUID(tool_version_id),
                arguments=arguments,
                allow_high_risk=self._allow_high_risk,
            )
        except ValidationFailedError as exc:
            return _failure(exc, "INPUT_INVALID")
        except ResourceUnavailableError as exc:
            return _failure(exc, "RESOURCE_UNAVAILABLE")
        except NotFoundError as exc:
            return _failure(exc, "RESOURCE_UNAVAILABLE")
        except HighRiskToolError as exc:
            return _failure(exc, "HIGH_RISK_BLOCKED")
        except ToolTimeoutError as exc:
            return _failure(exc, "TOOL_TIMEOUT")
        except ToolOutputInvalidError as exc:
            return _failure(exc, "TOOL_OUTPUT_INVALID")
        except ToolMcpError as exc:
            return _failure(exc, "TOOL_MCP_ERROR")
        except ToolExecutionError as exc:
            return _failure(exc, "TOOL_HTTP_ERROR")
        return ToolResult(
            ok=True,
            content=json.dumps(result.output, ensure_ascii=False)
            if not isinstance(result.output, str)
            else result.output,
        )
