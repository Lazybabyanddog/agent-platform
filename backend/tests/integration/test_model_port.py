"""ModelPort：OpenAI 兼容协议转换与错误归一化（mock 服务端）。"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from agent_platform.contexts.execution.domain.enums import ModelErrorCode
from agent_platform.contexts.execution.domain.ports import (
    ChatMessage,
    ModelPortError,
    ModelRequest,
)
from agent_platform.contexts.execution.infrastructure.llm.langchain_adapter import (
    LangChainModelAdapter,
)


def _completion(
    content: str = "hello",
    tool_calls: list[dict[str, Any]] | None = None,
    prompt_tokens: int = 10,
    completion_tokens: int = 5,
) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 1700000000,
        "model": "gpt-4o-mini",
        "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _request() -> ModelRequest:
    return ModelRequest(
        model_name="gpt-4o-mini",
        params={"temperature": 0.2, "max_tokens": 128},
        messages=[
            ChatMessage(role="system", content="be brief"),
            ChatMessage(role="user", content="hi"),
        ],
    )


async def test_success_returns_content_and_usage() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json=_completion())

    client = _client(handler)
    adapter = LangChainModelAdapter(
        base_url="http://mock.local/v1",
        api_key="sk-test",
        timeout_s=2,
        http_async_client=client,
    )
    result = await adapter.chat(_request())
    await client.aclose()

    assert result.content == "hello"
    assert (result.input_tokens, result.output_tokens) == (10, 5)
    assert captured["body"]["model"] == "gpt-4o-mini"
    assert captured["body"]["temperature"] == 0.2
    assert captured["auth"] == "Bearer sk-test"


async def test_tool_calls_are_converted_to_typed_arguments() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["tools"][0]["function"]["name"] == "echo"
        return httpx.Response(
            200,
            json=_completion(
                content="",
                tool_calls=[
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "echo", "arguments": '{"text": "hi"}'},
                    }
                ],
            ),
        )

    client = _client(handler)
    adapter = LangChainModelAdapter(
        base_url="http://mock.local/v1",
        api_key="sk-test",
        timeout_s=2,
        http_async_client=client,
    )
    from agent_platform.contexts.execution.domain.ports import ToolBinding

    result = await adapter.chat(
        ModelRequest(
            model_name="gpt-4o-mini",
            params={},
            messages=[ChatMessage(role="user", content="hi")],
            tools=[
                ToolBinding(
                    code="echo",
                    description="echo",
                    input_schema={"type": "object", "properties": {"text": {"type": "string"}}},
                )
            ],
        )
    )
    await client.aclose()
    assert result.tool_calls[0].name == "echo"
    assert result.tool_calls[0].arguments == {"text": "hi"}


async def test_rate_limit_maps_to_rate_limited() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            json={
                "error": {
                    "message": "rate limited",
                    "type": "rate_limit_error",
                    "code": "rate_limit_exceeded",
                }
            },
        )

    client = _client(handler)
    adapter = LangChainModelAdapter(
        base_url="http://mock.local/v1",
        api_key="sk-test",
        timeout_s=2,
        http_async_client=client,
    )
    with pytest.raises(ModelPortError) as caught:
        await adapter.chat(_request())
    await client.aclose()
    assert caught.value.code == ModelErrorCode.RATE_LIMITED


async def test_timeout_maps_to_timeout() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out", request=request)

    client = _client(handler)
    adapter = LangChainModelAdapter(
        base_url="http://mock.local/v1",
        api_key="sk-test",
        timeout_s=0.05,
        http_async_client=client,
    )
    with pytest.raises(ModelPortError) as caught:
        await adapter.chat(_request())
    await client.aclose()
    assert caught.value.code == ModelErrorCode.TIMEOUT
