"""MCP 客户端的窄封装。

协议与 SDK 迭代都快，这里只暴露两个接口：调用远端某个工具、列出远端声明的工具。
- 契约层（`McpSession`）与 `ToolService` / 探测用例解耦，测试可注入替身；
- 真实实现把 SDK 的两种远程传输收敛在一处，整个会话（建连 → initialize → 调用的那一步）包在超时内；
- 远端返回内容在这里归一为"可被 output_schema 校验的值"，不把二进制塞进结果；
- 工具清单归一为 `{name, description, input_schema}`，让上层不必认识 SDK 的模型类型。
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import time
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx
from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamablehttp_client

from agent_platform.shared.logging import get_logger

logger = get_logger(__name__)

_MAX_SUMMARY_CHARS = 500
_JSON_NULL_SENTINEL = object()


@dataclass(frozen=True, slots=True)
class McpCallResult:
    """一次 MCP 调用的归一化结果。"""

    output: Any
    remote_tool_name: str
    is_error: bool = False
    error_summary: str | None = None


@dataclass(frozen=True, slots=True)
class McpToolDescriptor:
    """远端声明的一个工具：探测结果的条目，也是注册时契约字段的来源。"""

    name: str
    description: str | None
    input_schema: dict[str, Any] | None


class McpSession(Protocol):
    """远端 MCP 工具调用与发现入口。"""

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

    async def list_tools(
        self,
        *,
        url: str,
        transport: str,
        headers: dict[str, str],
        timeout_s: float,
    ) -> list[McpToolDescriptor]: ...


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
        host, path = _endpoint_parts(url)
        started = time.perf_counter()
        connect_ms: int | None = None
        call_ms: int | None = None
        stage = "connect"
        # 整体超时：握手、initialize、call_tool 都受同一预算约束
        try:
            async with asyncio.timeout(timeout_s):
                connect_started = time.perf_counter()
                async with _open_transport(
                    url=url, transport=transport, headers=headers, timeout_s=timeout_s
                ) as streams:
                    # streamable_http 产出 (read, write, session_id_getter)，sse 产出 (read, write)
                    read_stream, write_stream = streams[0], streams[1]
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
                        connect_ms = _elapsed_ms(connect_started)
                        stage = "call"
                        call_started = time.perf_counter()
                        raw = await session.call_tool(tool_name, arguments)
                        call_ms = _elapsed_ms(call_started)
        except Exception as exc:  # noqa: BLE001 - 记录失败阶段后原样抛出
            total_ms = _elapsed_ms(started)
            logger.warning(
                "mcp.call",
                url_host=host,
                url_path=path,
                transport=transport,
                tool_name=tool_name,
                connect_ms=connect_ms if connect_ms is not None else total_ms,
                call_ms=call_ms or 0,
                is_error=True,
                failed_stage=stage,
                error=type(exc).__name__,
            )
            raise

        result = McpCallResult(
            output=normalize_content(getattr(raw, "content", None)),
            remote_tool_name=tool_name,
            is_error=bool(getattr(raw, "isError", False)),
            error_summary=(
                summarize_blocks(getattr(raw, "content", None))
                if getattr(raw, "isError", False)
                else None
            ),
        )
        emit = logger.warning if result.is_error else logger.info
        emit(
            "mcp.call",
            url_host=host,
            url_path=path,
            transport=transport,
            tool_name=tool_name,
            connect_ms=connect_ms or 0,
            call_ms=call_ms or 0,
            is_error=result.is_error,
        )
        return result

    async def list_tools(
        self,
        *,
        url: str,
        transport: str,
        headers: dict[str, str],
        timeout_s: float,
    ) -> list[McpToolDescriptor]:
        host, path = _endpoint_parts(url)
        started = time.perf_counter()
        # 与 call_tool 共用传输收敛点与同一份超时预算
        try:
            async with asyncio.timeout(timeout_s):
                async with _open_transport(
                    url=url, transport=transport, headers=headers, timeout_s=timeout_s
                ) as streams:
                    read_stream, write_stream = streams[0], streams[1]
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
                        listing = await session.list_tools()
        except Exception as exc:  # noqa: BLE001 - 记录失败后原样抛出
            logger.warning(
                "mcp.probe",
                url_host=host,
                url_path=path,
                transport=transport,
                tools_count=0,
                duration_ms=_elapsed_ms(started),
                ok=False,
                error=type(exc).__name__,
            )
            raise

        descriptors = [_descriptor(tool) for tool in getattr(listing, "tools", None) or []]
        logger.info(
            "mcp.probe",
            url_host=host,
            url_path=path,
            transport=transport,
            tools_count=len(descriptors),
            duration_ms=_elapsed_ms(started),
            ok=True,
        )
        return descriptors


def _endpoint_parts(url: str) -> tuple[str, str]:
    """日志里只出 host 与 path：查询串、userinfo 可能是凭据。"""
    parts = urlsplit(url)
    return (parts.hostname or "", parts.path or "/")


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}
_DEFAULT_HTTP_TIMEOUT_S = 30
_DEFAULT_SSE_READ_TIMEOUT_S = 300


def _open_transport(
    *, url: str, transport: str, headers: dict[str, str], timeout_s: float
):
    factory = _http_client_factory(url)
    if transport == "sse":
        return sse_client(
            url,
            headers=headers or None,
            timeout=timeout_s,
            sse_read_timeout=timeout_s,
            httpx_client_factory=factory,
        )
    return streamablehttp_client(
        url,
        headers=headers or None,
        timeout=timeout_s,
        sse_read_timeout=timeout_s,
        httpx_client_factory=factory,
    )


def _http_client_factory(url: str):
    """给 SDK 换一个 httpx 会话：本机/内网地址不走系统代理。

    SDK 默认的客户端 trust_env=True，会把 Windows 系统代理（以及 HTTP_PROXY）
    一并吃进来；本机地址一旦被转发到代理，代理多半直接回 502，表现为
    「服务明明在监听却探测不到」。外网 MCP 仍保留系统代理能力。
    """
    trust_env = not _is_local_endpoint(url)

    def factory(
        headers: dict[str, str] | None = None,
        timeout: httpx.Timeout | None = None,
        auth: httpx.Auth | None = None,
    ) -> httpx.AsyncClient:
        kwargs: dict[str, Any] = {
            "timeout": timeout
            or httpx.Timeout(_DEFAULT_HTTP_TIMEOUT_S, read=_DEFAULT_SSE_READ_TIMEOUT_S),
            "trust_env": trust_env,
        }
        if headers is not None:
            kwargs["headers"] = headers
        if auth is not None:
            kwargs["auth"] = auth
        return httpx.AsyncClient(**kwargs)

    return factory


def _is_local_endpoint(url: str) -> bool:
    """回环与私网视为本机/内网：这些地址没有走代理的意义。"""
    host = (urlsplit(url).hostname or "").strip("[]").lower()
    if not host:
        return False
    if host in _LOOPBACK_HOSTS:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or address.is_private


def _descriptor(tool: Any) -> McpToolDescriptor:
    """把 SDK 的工具模型收敛成平台自己的三元结构；字段缺失时降级为 None。"""
    name = str(getattr(tool, "name", "") or "")
    description = getattr(tool, "description", None)
    schema = getattr(tool, "inputSchema", None)
    return McpToolDescriptor(
        name=name,
        description=str(description) if description else None,
        input_schema=schema if isinstance(schema, dict) else None,
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


__all__ = [
    "McpCallResult",
    "McpSession",
    "McpToolDescriptor",
    "SdkMcpSession",
    "normalize_content",
    "summarize_blocks",
]
