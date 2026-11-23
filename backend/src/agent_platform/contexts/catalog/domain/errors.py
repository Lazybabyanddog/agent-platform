from __future__ import annotations

from typing import Any

from agent_platform.shared.errors import AppError


class ToolExecutionError(AppError):
    code = "TOOL_HTTP_ERROR"
    http_status = 502
    message = "tool call failed"


class ToolTimeoutError(AppError):
    code = "TOOL_TIMEOUT"
    http_status = 504
    message = "tool call timed out"


class ToolOutputInvalidError(AppError):
    code = "TOOL_OUTPUT_INVALID"
    http_status = 502
    message = "tool response does not match its output schema"


MCP_ERROR_SUMMARY_LIMIT = 500


class ToolMcpError(AppError):
    """远端 MCP 工具调用失败。

    对外只给稳定错误码与笼统消息；远端原始响应只以截断摘要放进 details 供排查，
    不整段回传。
    """

    code = "TOOL_MCP_ERROR"
    http_status = 502
    message = "remote mcp tool call failed"

    def __init__(
        self,
        message: str | None = None,
        *,
        remote_tool_name: str | None = None,
        remote_error: str | None = None,
        details: list[dict[str, Any]] | None = None,
    ) -> None:
        merged = list(details or [])
        if remote_tool_name is not None:
            merged.append({"field": "mcp.tool_name", "reason": remote_tool_name})
        if remote_error:
            merged.append(
                {
                    "field": "mcp.remote_error",
                    "reason": _truncate(remote_error, MCP_ERROR_SUMMARY_LIMIT),
                }
            )
        super().__init__(message, details=merged)


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else f"{text[:limit]}..."


class HighRiskToolError(AppError):
    code = "TOOL_RISK_BLOCKED"
    http_status = 403
    message = "high-risk tool requires explicit approval"

    def __init__(self, message: str | None = None, *, details: list[dict[str, Any]] | None = None) -> None:
        super().__init__(message, details=details)
