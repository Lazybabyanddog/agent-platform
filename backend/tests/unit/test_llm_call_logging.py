"""模型调用日志：``llm.call`` 摘要、``llm.upstream_error`` 归一错误，且不出正文。"""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx
import openai
import pytest

from agent_platform.contexts.execution.domain.enums import ModelErrorCode
from agent_platform.contexts.execution.domain.ports import (
    ModelPortError,
    ModelRequest,
    ModelResponse,
)
from agent_platform.contexts.execution.infrastructure.llm.langchain_adapter import (
    LangChainModelAdapter,
)
from agent_platform.contexts.execution.infrastructure.tracing import TracingModelPort
from agent_platform.settings import Settings
from agent_platform.shared.logging import configure_logging, reset_logging

SECRET_PROMPT = "PROMPT-BODY-SECRET"
SECRET_ANSWER = "ANSWER-BODY-SECRET"


@pytest.fixture(autouse=True)
def _clean_logging():
    reset_logging()
    configure_logging(Settings(log_level="INFO", log_json=True), service="api")
    yield
    reset_logging()


def _records(captured: str) -> list[dict]:
    return [json.loads(line) for line in captured.splitlines() if line.startswith("{")]


class _Inner:
    def __init__(self, response: ModelResponse | None = None, error: Exception | None = None):
        self._response = response
        self._error = error

    async def chat(self, _request: ModelRequest) -> ModelResponse:
        if self._error is not None:
            raise self._error
        assert self._response is not None
        return self._response


class _Recorder:
    def __init__(self, fail: bool = False) -> None:
        self.records: list[Any] = []
        self._fail = fail

    async def record_llm_call(self, record: Any) -> None:
        if self._fail:
            raise RuntimeError("trace storage down")
        self.records.append(record)


def _port(inner: _Inner, recorder: _Recorder) -> TracingModelPort:
    return TracingModelPort(
        inner,  # type: ignore[arg-type]
        recorder=recorder,  # type: ignore[arg-type]
        run_id=uuid.uuid4(),
        trace_id="trace-1",
        model_id=uuid.uuid4(),
        model_name="gpt-4o-mini",
    )


def _request() -> ModelRequest:
    return ModelRequest(model_name="gpt-4o-mini", params={}, messages=[])


async def test_success_logs_summary_only(capsys: pytest.CaptureFixture[str]) -> None:
    recorder = _Recorder()
    port = _port(
        _Inner(
            ModelResponse(content=SECRET_ANSWER, input_tokens=11, output_tokens=5)
        ),
        recorder,
    )

    await port.chat(_request())

    captured = capsys.readouterr().out
    records = _records(captured)
    assert [item["event"] for item in records] == ["llm.call"]
    record = records[0]
    assert record["model_name"] == "gpt-4o-mini"
    assert record["input_tokens"] == 11
    assert record["output_tokens"] == 5
    assert record["error_code"] is None
    assert isinstance(record["latency_ms"], int)
    assert SECRET_ANSWER not in captured
    assert SECRET_PROMPT not in captured


async def test_failure_logs_normalized_code(capsys: pytest.CaptureFixture[str]) -> None:
    recorder = _Recorder()
    port = _port(
        _Inner(error=ModelPortError(ModelErrorCode.TIMEOUT, SECRET_PROMPT)), recorder
    )

    with pytest.raises(ModelPortError):
        await port.chat(_request())

    record = _records(capsys.readouterr().out)[0]
    assert record["event"] == "llm.call"
    assert record["error_code"] == ModelErrorCode.TIMEOUT.value
    assert record["input_tokens"] == 0


async def test_recorder_failure_keeps_result(capsys: pytest.CaptureFixture[str]) -> None:
    recorder = _Recorder(fail=True)
    port = _port(_Inner(ModelResponse(content="ok")), recorder)

    response = await port.chat(_request())

    assert response.content == "ok"
    events = [item["event"] for item in _records(capsys.readouterr().out)]
    assert events == ["llm.call", "trace.llm_write_failed"]


def _http_request() -> httpx.Request:
    return httpx.Request("POST", "https://api.example.com/v1/chat/completions")


def _adapter(fake_client: Any) -> LangChainModelAdapter:
    adapter = LangChainModelAdapter(base_url="http://localhost", api_key="k", timeout_s=1)
    adapter._client = lambda *_args, **_kwargs: fake_client  # type: ignore[method-assign]
    return adapter


class _FailingClient:
    def __init__(self, error: Exception) -> None:
        self._error = error

    async def ainvoke(self, _messages: Any) -> Any:
        raise self._error


async def test_upstream_rate_limit_logs_status(capsys: pytest.CaptureFixture[str]) -> None:
    error = openai.RateLimitError(
        "rate limited for key sk-secret",
        response=httpx.Response(429, request=_http_request()),
        body=None,
    )
    adapter = _adapter(_FailingClient(error))

    with pytest.raises(ModelPortError) as info:
        await adapter.chat(_request())

    assert info.value.code is ModelErrorCode.RATE_LIMITED
    captured = capsys.readouterr().out
    record = _records(captured)[0]
    assert record["event"] == "llm.upstream_error"
    assert record["error_code"] == ModelErrorCode.RATE_LIMITED.value
    assert record["upstream_status"] == 429
    assert record["model_name"] == "gpt-4o-mini"
    assert "sk-secret" not in captured


async def test_upstream_timeout_has_no_status(capsys: pytest.CaptureFixture[str]) -> None:
    adapter = _adapter(_FailingClient(openai.APITimeoutError(request=_http_request())))

    with pytest.raises(ModelPortError) as info:
        await adapter.chat(_request())

    assert info.value.code is ModelErrorCode.TIMEOUT
    record = _records(capsys.readouterr().out)[0]
    assert record["event"] == "llm.upstream_error"
    assert record["upstream_status"] is None
