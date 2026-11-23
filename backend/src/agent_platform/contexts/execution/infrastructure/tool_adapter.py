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
            return ToolResult(ok=False, content=exc.message, error_code="INPUT_INVALID")
        except ResourceUnavailableError as exc:
            return ToolResult(ok=False, content=exc.message, error_code="RESOURCE_UNAVAILABLE")
        except NotFoundError as exc:
            return ToolResult(ok=False, content=exc.message, error_code="RESOURCE_UNAVAILABLE")
        except HighRiskToolError as exc:
            return ToolResult(ok=False, content=exc.message, error_code="HIGH_RISK_BLOCKED")
        except ToolTimeoutError as exc:
            return ToolResult(ok=False, content=exc.message, error_code="TOOL_TIMEOUT")
        except ToolOutputInvalidError as exc:
            return ToolResult(ok=False, content=exc.message, error_code="TOOL_OUTPUT_INVALID")
        except ToolMcpError as exc:
            return ToolResult(ok=False, content=exc.message, error_code="TOOL_MCP_ERROR")
        except ToolExecutionError as exc:
            return ToolResult(ok=False, content=exc.message, error_code="TOOL_HTTP_ERROR")
        return ToolResult(
            ok=True,
            content=json.dumps(result.output, ensure_ascii=False)
            if not isinstance(result.output, str)
            else result.output,
        )
