"""MCP 工具探测：注册前连远端取回工具清单。

探测是只读辅助能力——不建工具、不出版本、不写调用记录。它的价值在于
`tool_name` 与 `input_schema` 不必人工抄录，那是接一个 MCP 服务最容易错的一步。

结论在这里收敛为 `McpProbeVerdict`，远端原始响应不出这一层，与工具执行保持同一口径。
"""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Any

import httpx

from agent_platform.contexts.catalog.application.tool_service import build_auth_headers
from agent_platform.contexts.catalog.domain.enums import McpProbeVerdict
from agent_platform.contexts.catalog.domain.tool import validate_mcp_endpoint
from agent_platform.contexts.catalog.infrastructure.mcp_client import (
    McpSession,
    McpToolDescriptor,
    SdkMcpSession,
)
from agent_platform.shared.context import ActorContext

PROBE_SUMMARY_LIMIT = 200


@dataclass(frozen=True, slots=True)
class McpProbeResult:
    verdict: McpProbeVerdict
    tools: list[McpToolDescriptor]
    latency_ms: int
    error_summary: str | None = None


class McpProbeService:
    def __init__(self, *, mcp_session: McpSession | None = None) -> None:
        self._mcp_session = mcp_session or SdkMcpSession()

    async def probe(self, *, actor: ActorContext, spec: dict[str, Any]) -> McpProbeResult:
        """连远端取工具清单。

        端点校验与保存共用 `validate_mcp_endpoint`：探测发生在选定 `tool_name` 之前，
        拿不到完整契约，但「连到哪里、怎么连」的规则必须与保存时一致。
        """
        endpoint = validate_mcp_endpoint(spec)
        # 鉴权解析与执行走同一条路径，避免探测与真正调用各自演化成两套
        headers = await build_auth_headers(tenant_id=actor.tenant_id, spec={**spec, **endpoint})

        started = perf_counter()
        try:
            tools = await self._mcp_session.list_tools(
                url=endpoint["url"],
                transport=endpoint["transport"],
                headers=headers,
                timeout_s=endpoint["timeout_s"],
            )
        except Exception as exc:
            # 远端/协议层的任何异常都收敛成结论；不把原始响应体交给调用方
            return McpProbeResult(
                verdict=_classify(exc),
                tools=[],
                latency_ms=_elapsed_ms(started),
                error_summary=_summarize(exc),
            )

        return McpProbeResult(
            verdict=McpProbeVerdict.OK,
            tools=list(tools),
            latency_ms=_elapsed_ms(started),
        )


def _classify(exc: BaseException) -> McpProbeVerdict:
    """按「超时 → 认证 → 不可达」的优先级归一，保证同一异常总落到同一结论。"""
    leaves = _leaf_exceptions(exc)
    if any(isinstance(leaf, TimeoutError | httpx.TimeoutException) for leaf in leaves):
        return McpProbeVerdict.MCP_PROBE_TIMEOUT
    if any(_is_auth_rejection(leaf) for leaf in leaves):
        return McpProbeVerdict.MCP_PROBE_AUTH_FAILED
    if any(
        isinstance(leaf, httpx.ConnectError | ConnectionError) for leaf in leaves
    ):
        return McpProbeVerdict.MCP_PROBE_UNREACHABLE
    return McpProbeVerdict.MCP_PROBE_PROTOCOL_ERROR


def _is_auth_rejection(exc: BaseException) -> bool:
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (401, 403)


def _leaf_exceptions(exc: BaseException) -> list[BaseException]:
    """anyio 的任务组会把真实异常裹进 ExceptionGroup，判定前先摊平。"""
    if isinstance(exc, BaseExceptionGroup):
        leaves: list[BaseException] = []
        for sub in exc.exceptions:
            leaves.extend(_leaf_exceptions(sub))
        return leaves
    return [exc]


def _elapsed_ms(started: float) -> int:
    return max(int((perf_counter() - started) * 1000), 0)


def _summarize(exc: BaseException) -> str:
    """只给截断摘要：远端原始响应体不出这一层。"""
    text = f"{type(exc).__name__}: {exc}".strip().rstrip(":")
    return text if len(text) <= PROBE_SUMMARY_LIMIT else f"{text[:PROBE_SUMMARY_LIMIT]}..."


mcp_probe_service = McpProbeService()
