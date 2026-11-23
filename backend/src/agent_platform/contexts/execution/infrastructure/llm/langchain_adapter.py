"""OpenAI 兼容模型的 LangChain 适配器。

只负责协议转换与错误归一化；重试与 fallback 不在此层实现。
"""

from __future__ import annotations

import json
from typing import Any

import openai
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI

from agent_platform.contexts.execution.domain.enums import ModelErrorCode
from agent_platform.contexts.execution.domain.ports import (
    ChatMessage,
    ModelPortError,
    ModelRequest,
    ModelResponse,
    ToolCall,
)

_ALLOWED_PARAMS = {
    "temperature",
    "max_tokens",
    "max_completion_tokens",
    "top_p",
    "presence_penalty",
    "frequency_penalty",
    "stop",
    "seed",
}


def _to_lc_messages(messages: list[ChatMessage]) -> list[Any]:
    converted: list[Any] = []
    for message in messages:
        if message.role == "system":
            converted.append(SystemMessage(content=message.content))
        elif message.role == "user":
            converted.append(HumanMessage(content=message.content))
        elif message.role == "assistant":
            converted.append(
                AIMessage(
                    content=message.content,
                    tool_calls=[
                        {"name": call.name, "args": call.arguments, "id": call.id}
                        for call in message.tool_calls
                    ],
                )
            )
        elif message.role == "tool":
            converted.append(
                ToolMessage(
                    content=message.content,
                    tool_call_id=message.tool_call_id or "",
                )
            )
        else:
            raise ModelPortError(ModelErrorCode.INVALID_REQUEST, f"unsupported role {message.role!r}")
    return converted


def _tool_schemas(request: ModelRequest) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": binding.code,
                "description": binding.description or "",
                "parameters": binding.input_schema,
            },
        }
        for binding in request.tools
    ]


def _normalize_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return json.dumps(content, ensure_ascii=False)
    return "" if content is None else str(content)


def _token_usage(response: AIMessage) -> tuple[int, int]:
    metadata = getattr(response, "usage_metadata", None) or {}
    if metadata:
        return int(metadata.get("input_tokens") or 0), int(metadata.get("output_tokens") or 0)
    raw = (response.response_metadata or {}).get("token_usage") or {}
    return int(raw.get("prompt_tokens") or 0), int(raw.get("completion_tokens") or 0)


class LangChainModelAdapter:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout_s: float,
        http_async_client: Any | None = None,
    ) -> None:
        self._base_url = base_url
        self._api_key = api_key
        self._timeout_s = timeout_s
        self._http_async_client = http_async_client

    def _client(self, model_name: str, params: dict[str, Any]) -> ChatOpenAI:
        kwargs = {key: value for key, value in params.items() if key in _ALLOWED_PARAMS}
        return ChatOpenAI(
            model=model_name,
            base_url=self._base_url,
            api_key=self._api_key,
            timeout=self._timeout_s,
            max_retries=0,
            http_async_client=self._http_async_client,
            **kwargs,
        )

    async def chat(self, request: ModelRequest) -> ModelResponse:
        client = self._client(request.model_name, request.params)
        messages = _to_lc_messages(request.messages)
        try:
            if request.tools:
                client = client.bind_tools(_tool_schemas(request))
            message: AIMessage = await client.ainvoke(messages)
        except openai.RateLimitError as exc:
            raise ModelPortError(ModelErrorCode.RATE_LIMITED, str(exc)) from exc
        except (openai.APITimeoutError, TimeoutError) as exc:
            raise ModelPortError(ModelErrorCode.TIMEOUT, str(exc)) from exc
        except (openai.AuthenticationError, openai.PermissionDeniedError) as exc:
            raise ModelPortError(ModelErrorCode.AUTH, str(exc)) from exc
        except (openai.BadRequestError, openai.UnprocessableEntityError) as exc:
            raise ModelPortError(ModelErrorCode.INVALID_REQUEST, str(exc)) from exc
        except openai.APIConnectionError as exc:
            raise ModelPortError(ModelErrorCode.UPSTREAM, str(exc)) from exc
        except openai.APIStatusError as exc:
            raise ModelPortError(ModelErrorCode.UPSTREAM, str(exc)) from exc
        except ModelPortError:
            raise
        except Exception as exc:  # pragma: no cover - 兜底避免把未知异常漏到执行层
            raise ModelPortError(ModelErrorCode.UNKNOWN, str(exc)) from exc

        input_tokens, output_tokens = _token_usage(message)
        tool_calls: list[ToolCall] = []
        for call in message.tool_calls or []:
            arguments = call.get("args")
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError:
                    arguments = {}
            tool_calls.append(
                ToolCall(
                    id=str(call.get("id") or ""),
                    name=str(call.get("name") or ""),
                    arguments=dict(arguments or {}),
                )
            )
        return ModelResponse(
            content=_normalize_content(message.content),
            tool_calls=tuple(tool_calls),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
