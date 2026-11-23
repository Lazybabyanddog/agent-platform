"""MCP 客户端的窄封装。

协议与 SDK 迭代都快，这里只暴露一个"调用远端某个工具"的接口：
- 契约层（`McpSession`）与 `ToolService` 解耦，测试可注入替身；
- 真实实现把 SDK 的两种远程传输收敛在一处，整个会话（建连 → initialize → call_tool）包在超时内；
- 远端返回内容在这里归一为"可被 output_schema 校验的值"，不把二进制塞进结果。
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Protocol

from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamablehttp_client

_MAX_SUMMARY_CHARS = 500
_JSON_NULL_SENTINEL = object()


@dataclass(frozen=True, slots=True)
class McpCallResult:
    """一次 MCP 调用的归一化结果。"""

    output: Any
    remote_tool_name: str
    is_error: bool = False
    error_summary: str | None = None


class McpSession(Protocol):
    """远端 MCP 工具调用入口。"""

    async def call_tool(
        self,
        *,
        url: str,
        transport: str,
        tool_name: str,
        arguments: dict[str, Any],
        headers: dict[str, str],
        timeout_s: float,
    ) -> McpCallResult: ...


class SdkMcpSession:
    """基于官方 mcp SDK 的实现：每次调用新建会话，不做连接复用。"""

    async def call_tool(
        self,
        *,
        url: str,
        transport: str,
        tool_name: str,
        arguments: dict[str, Any],
        headers: dict[str, str],
        timeout_s: float,
    ) -> McpCallResult:
        # 整体超时：握手、initialize、call_tool 都受同一预算约束
        async with asyncio.timeout(timeout_s):
            async with _open_transport(
                url=url, transport=transport, headers=headers, timeout_s=timeout_s
            ) as streams:
                # streamable_http 产出 (read, write, session_id_getter)，sse 产出 (read, write)
                read_stream, write_stream = streams[0], streams[1]
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    raw = await session.call_tool(tool_name, arguments)

        return McpCallResult(
            output=normalize_content(getattr(raw, "content", None)),
            remote_tool_name=tool_name,
            is_error=bool(getattr(raw, "isError", False)),
            error_summary=(
                summarize_blocks(getattr(raw, "content", None))
                if getattr(raw, "isError", False)
                else None
            ),
        )


def _open_transport(
    *, url: str, transport: str, headers: dict[str, str], timeout_s: float
):
    if transport == "sse":
        return sse_client(
            url,
            headers=headers or None,
            timeout=timeout_s,
            sse_read_timeout=timeout_s,
        )
    return streamablehttp_client(
        url,
        headers=headers or None,
        timeout=timeout_s,
        sse_read_timeout=timeout_s,
    )


def normalize_content(blocks: Any) -> Any:
    """把远端 content 数组归一为结构化或文本结果。

    1. 全部为 text：单条且可 JSON 解析时取解析值，否则拼接为一个字符串；
    2. 含 image/resource 等非文本内容：转为 `{"content": [{"type", "summary"}, ...]}` 摘要。
    """
    items = list(blocks or [])
    if not items:
        return ""

    if all(_kind(item) == "text" for item in items):
        text = "\n".join(str(getattr(item, "text", "")) for item in items)
        if len(items) == 1:
            parsed = _try_json(text)
            if parsed is not _JSON_NULL_SENTINEL:
                return parsed
        return text

    return {"content": [_block_summary(item) for item in items]}


def summarize_blocks(blocks: Any) -> str:
    """错误摘要：只保留可读文本或类型标识，长度受限。"""
    parts: list[str] = []
    for item in blocks or []:
        if _kind(item) == "text":
            parts.append(str(getattr(item, "text", "")))
        else:
            parts.append(f"<{_kind(item)}>")
    return _truncate("\n".join(parts))


def _try_json(text: str) -> Any:
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return _JSON_NULL_SENTINEL


def _kind(item: Any) -> str:
    return str(getattr(item, "type", "unknown"))


def _block_summary(item: Any) -> dict[str, Any]:
    kind = _kind(item)
    if kind == "text":
        return {"type": "text", "summary": _truncate(str(getattr(item, "text", "")))}
    payload = None
    dump = getattr(item, "model_dump", None)
    if callable(dump):
        try:
            payload = dump(exclude_none=True)
        except Exception:  # noqa: BLE001 - 摘要失败不影响结果
            payload = None
    if payload is None:
        payload = {"type": kind}
    return {"type": kind, "summary": _truncate(json.dumps(payload, ensure_ascii=False, default=str))}


def _truncate(text: str) -> str:
    if len(text) <= _MAX_SUMMARY_CHARS:
        return text
    return f"{text[:_MAX_SUMMARY_CHARS]}..."


__all__ = ["McpCallResult", "McpSession", "SdkMcpSession", "normalize_content", "summarize_blocks"]
