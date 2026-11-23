"""进程内真实 MCP server（streamable_http），用于验证协议级行为。

用官方 SDK 的 FastMCP + uvicorn 在后台线程起服务，端点固定在 `/mcp`，
工具 `search_documents` 的结果完全确定，便于断言"平台真的按 MCP 协议调通了"。
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import uvicorn
from mcp.server.fastmcp import FastMCP

DOCUMENTS = [
    {"id": "doc-1", "title": "退款政策", "score": 0.93},
    {"id": "doc-2", "title": "发票开具说明", "score": 0.81},
]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_until_listening(port: int, *, timeout_s: float = 15.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError(f"mcp test server did not start on port {port}")


def build_server() -> FastMCP:
    server = FastMCP("test-knowledge")

    @server.tool()
    def search_documents(query: str, limit: int = 2) -> dict[str, Any]:
        """按关键词检索知识库文档。"""
        hits = [item for item in DOCUMENTS if query.strip()][: max(limit, 0)]
        return {"query": query, "hits": len(hits), "documents": hits}

    return server


@contextmanager
def local_mcp_server() -> Iterator[str]:
    """起一个本地 MCP server，产出可被客户端直接使用的 URL。"""
    port = _free_port()
    app = build_server().streamable_http_app()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", lifespan="on")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=lambda: asyncio.run(server.serve()), daemon=True)
    thread.start()
    try:
        _wait_until_listening(port)
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
