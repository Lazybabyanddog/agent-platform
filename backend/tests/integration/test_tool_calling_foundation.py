"""模型可见契约、工具纠错回路与运行时上下文的端到端接线。

这些用例验证的是「模型实际看到了什么、平台在失败后做了什么」，
因此断言都落在真实请求与真实落库记录上，而非内部函数返回值。
"""

from __future__ import annotations

import json
import re
import uuid

import pytest
from sqlalchemy import text

from agent_platform.contexts.execution.application.invocation_service import invocation_service
from agent_platform.contexts.execution.domain.ports import (
    ModelRequest,
    ModelResponse,
    ToolCall,
    ToolResult,
)
from agent_platform.contexts.execution.infrastructure.checkpoint_store import checkpoint_store
from agent_platform.contexts.insight.infrastructure.repositories import LLMCallRepository
from agent_platform.shared.db import session_scope
from tests.integration.agent_helpers import (
    create_agent,
    create_agent_version,
    create_model,
    create_prompt_version,
    suffix,
)
from tests.integration.conftest import TenantEnv

TOOL_SPEC = {
    "method": "POST",
    "url": "https://tools.local/weather",
    "timeout_s": 5,
    "description": "查询指定城市某日天气",
    "input_schema": {
        "type": "object",
        "properties": {
            "city": {"type": "string"},
            "start_date": {"type": "string"},
        },
        "required": ["city", "start_date"],
    },
    "output_schema": {"type": "object"},
    "model_schema": {
        "type": "object",
        "properties": {
            "city": {"type": "string", "description": "中文城市名，如 武汉"},
            "start_date": {
                "type": "string",
                "description": "开始日期",
                "format": "date",
                "examples": ["2026-11-25"],
            },
        },
        "required": ["city", "start_date"],
    },
    "returns": {"terminal_statuses": ["no_data"]},
}

VALID_DATE = "2026-11-25"


class CapturingModel:
    def __init__(self, responses: list[ModelResponse]) -> None:
        self._responses = list(responses)
        self.requests: list[ModelRequest] = []

    async def chat(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return self._responses.pop(0)


class EchoTool:
    def __init__(self, *results: ToolResult) -> None:
        self._results = list(results)
        self.calls: list[dict] = []

    async def invoke(self, *, tool_code, tool_version_id, arguments):
        del tool_code, tool_version_id
        self.calls.append(arguments)
        return self._results.pop(0)


def _answer(text: str = "好的") -> ModelResponse:
    return ModelResponse(content=text, input_tokens=5, output_tokens=2)


def _call(tool_code: str, arguments: dict, call_id: str = "call-1") -> ModelResponse:
    return ModelResponse(
        content="",
        tool_calls=(ToolCall(id=call_id, name=tool_code, arguments=arguments),),
        input_tokens=5,
        output_tokens=2,
    )


def use_model(monkeypatch: pytest.MonkeyPatch, model) -> None:
    async def factory(actor, snapshot, timeout_s):
        del actor, snapshot, timeout_s
        return model

    monkeypatch.setattr(invocation_service, "_model_factory", factory)


def use_tool(monkeypatch: pytest.MonkeyPatch, tool) -> None:
    monkeypatch.setattr(invocation_service, "_tool_factory", lambda actor: tool)


async def _create_contract_tool(api_client, headers: dict[str, str]) -> dict:
    created = await api_client.post(
        "/api/v1/tools",
        headers=headers,
        json={
            "code": f"weather-{suffix()}",
            "name": "Weather",
            "description": "天气查询",
            "spec": TOOL_SPEC,
        },
    )
    assert created.status_code == 201, created.text
    tool_id = created.json()["id"]
    granted = await api_client.post(f"/api/v1/tools/{tool_id}/grant", headers=headers)
    assert granted.status_code == 204, granted.text
    versions = await api_client.get(f"/api/v1/tools/{tool_id}/versions", headers=headers)
    assert versions.status_code == 200, versions.text
    return {**versions.json()[0], "tool_code": created.json()["code"]}


async def _create_qa_prompt_version(api_client, headers: dict[str, str]) -> dict:
    created = await api_client.post(
        "/api/v1/prompts",
        headers=headers,
        json={
            "code": f"qa-{suffix()}",
            "name": "QA",
            "messages": [
                {"role": "system", "content": "你是助手"},
                {"role": "user", "content": "Q: {{q}}"},
            ],
            "variables": [{"name": "q", "type": "string", "required": True}],
        },
    )
    assert created.status_code == 201, created.text
    versions = await api_client.get(
        f"/api/v1/prompts/{created.json()['id']}/versions", headers=headers
    )
    assert versions.status_code == 200, versions.text
    return versions.json()[0]


async def _published_agent(
    api_client,
    headers: dict[str, str],
    *,
    tool: dict | None = None,
    policy: dict | None = None,
    runtime_context: list[str] | None = None,
    prompt_version: dict | None = None,
) -> tuple[dict, dict]:
    model = await create_model(api_client, headers)
    prompt = prompt_version or await create_prompt_version(api_client, headers)
    agent = await create_agent(api_client, headers)
    version = await create_agent_version(
        api_client,
        headers,
        agent["id"],
        model_id=model["id"],
        prompt_version_id=prompt["id"],
        tool_version_ids=[tool["id"]] if tool else [],
        policy=policy,
        runtime_context=runtime_context,
    )
    released = await api_client.post(
        f"/api/v1/agents/{agent['id']}/releases",
        headers=headers,
        json={"agent_version_id": version["id"], "env": "DEV"},
    )
    assert released.status_code == 201, released.text
    return agent, version


async def _invoke(api_client, headers: dict[str, str], agent_id: str, **payload) -> dict:
    response = await api_client.post(
        f"/api/v1/agents/{agent_id}/invoke",
        headers=headers,
        json={"env": "DEV", "input": {}, **payload},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _trace(api_client, headers: dict[str, str], run_id: str) -> dict:
    response = await api_client.get(f"/api/v1/runs/{run_id}/trace", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------- 模型可见契约


async def test_tool_card_carries_description_and_date_format(
    api_client,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool = await _create_contract_tool(api_client, admin_headers)
    agent, _ = await _published_agent(api_client, admin_headers, tool=tool)
    model = CapturingModel(
        [
            _call(tool["tool_code"], {"city": "武汉", "start_date": VALID_DATE}),
            _answer("晴"),
        ]
    )
    use_model(monkeypatch, model)
    use_tool(monkeypatch, EchoTool(ToolResult(ok=True, content=json.dumps({"ok": True}))))

    body = await _invoke(api_client, admin_headers, agent["id"])
    assert body["status"] == "SUCCESS"

    card = model.requests[0].tools[0]
    assert card.description == "查询指定城市某日天气"
    assert card.input_schema["properties"]["start_date"]["format"] == "date"
    assert card.input_schema["properties"]["city"]["description"] == "中文城市名，如 武汉"


async def test_trace_keeps_tool_card_and_tool_count(
    api_client,
    tenant_env: TenantEnv,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool = await _create_contract_tool(api_client, admin_headers)
    agent, _ = await _published_agent(api_client, admin_headers, tool=tool)
    use_model(
        monkeypatch,
        CapturingModel(
            [_call(tool["tool_code"], {"city": "武汉", "start_date": VALID_DATE}), _answer()]
        ),
    )
    use_tool(monkeypatch, EchoTool(ToolResult(ok=True, content="{}")))

    body = await _invoke(api_client, admin_headers, agent["id"])
    trace = await _trace(api_client, admin_headers, body["run_id"])
    llm_entries = [entry for entry in trace["entries"] if entry["type"] == "LLM_CALL"]

    assert [entry["summary"]["tool_count"] for entry in llm_entries] == [1, 1]
    request = llm_entries[0]["content"]["request"]
    assert request["tool_count"] == 1
    assert request["tools"][0]["name"] == tool["tool_code"]
    assert request["tools"][0]["description"] == "查询指定城市某日天气"
    assert request["tools"][0]["parameters"]["properties"]["start_date"]["format"] == "date"

    async with session_scope(tenant_env.tenant_id) as session:
        calls = await LLMCallRepository().list_for_run(
            session, tenant_id=tenant_env.tenant_id, run_id=uuid.UUID(body["run_id"])
        )
    assert [call.tool_count for call in calls] == [1, 1]


async def test_trace_shows_zero_tool_count_without_tools(
    api_client,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent, _ = await _published_agent(api_client, admin_headers)
    use_model(monkeypatch, CapturingModel([_answer()]))

    body = await _invoke(api_client, admin_headers, agent["id"])
    trace = await _trace(api_client, admin_headers, body["run_id"])

    assert trace["entries"][0]["summary"]["tool_count"] == 0


# ---------------------------------------------------------------- 纠错回路


async def test_recoverable_failure_returns_envelope_and_is_retried(
    api_client,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool = await _create_contract_tool(api_client, admin_headers)
    agent, _ = await _published_agent(api_client, admin_headers, tool=tool)
    model = CapturingModel(
        [
            _call(tool["tool_code"], {"city": "武汉", "start_date": "11/25"}, call_id="call-1"),
            _call(tool["tool_code"], {"city": "武汉", "start_date": VALID_DATE}, call_id="call-2"),
            _answer("晴"),
        ]
    )
    use_model(monkeypatch, model)
    use_tool(
        monkeypatch,
        EchoTool(
            ToolResult(
                ok=False,
                content="start_date must be an ISO date",
                error_code="INPUT_INVALID",
                details=({"field": "start_date", "reason": "expected YYYY-MM-DD"},),
                recoverable=True,
            ),
            ToolResult(ok=True, content="{}"),
        ),
    )

    body = await _invoke(api_client, admin_headers, agent["id"])
    assert body["status"] == "SUCCESS"

    retry_request = model.requests[1]
    envelope = json.loads(
        [item for item in retry_request.messages if item.role == "tool"][-1].content
    )
    assert envelope["status"] == "error"
    assert envelope["recoverable"] is True
    assert envelope["code"] == "INPUT_INVALID"
    assert envelope["fields"] == [{"field": "start_date", "reason": "expected YYYY-MM-DD"}]
    assert "message" in envelope and envelope["message"]
    assert envelope["hint"]

    trace = await _trace(api_client, admin_headers, body["run_id"])
    tool_entries = [entry for entry in trace["entries"] if entry["type"] == "TOOL_CALL"]
    assert [entry["status"] for entry in tool_entries] == ["FAILED", "SUCCESS"]
    assert tool_entries[0]["error_code"] == "INPUT_INVALID"


async def test_unrecoverable_tool_failure_stops_the_run(
    api_client,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool = await _create_contract_tool(api_client, admin_headers)
    agent, _ = await _published_agent(api_client, admin_headers, tool=tool)
    model = CapturingModel(
        [_call(tool["tool_code"], {"city": "武汉", "start_date": VALID_DATE}), _answer()]
    )
    use_model(monkeypatch, model)
    use_tool(
        monkeypatch,
        EchoTool(
            ToolResult(
                ok=False,
                content="high risk tool requires approval",
                error_code="HIGH_RISK_BLOCKED",
                recoverable=False,
            )
        ),
    )

    body = await _invoke(api_client, admin_headers, agent["id"])
    assert body["status"] == "FAILED"
    assert body["error"]["code"] == "TOOL_ERROR"
    assert body["error"]["details"]["tool_error_code"] == "HIGH_RISK_BLOCKED"
    assert len(model.requests) == 1


async def test_retry_loop_is_bounded_by_step_budget(
    api_client,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool = await _create_contract_tool(api_client, admin_headers)
    agent, _ = await _published_agent(
        api_client,
        admin_headers,
        tool=tool,
        policy={"max_steps": 3, "token_budget": 10000, "timeout_s": 5},
    )

    class NeverFixesModel:
        """每次换一个参数，既不复用上一条失败记录，也不收敛。"""

        def __init__(self) -> None:
            self.calls = 0
            self.requests: list[ModelRequest] = []

        async def chat(self, request: ModelRequest) -> ModelResponse:
            self.requests.append(request)
            self.calls += 1
            return ModelResponse(
                content="",
                tool_calls=(
                    ToolCall(
                        id=f"call-{self.calls}",
                        name=tool["tool_code"],
                        arguments={"city": f"city-{self.calls}", "start_date": VALID_DATE},
                    ),
                ),
                input_tokens=5,
                output_tokens=2,
            )

    model = NeverFixesModel()
    tool_stub = EchoTool(
        *[
            ToolResult(ok=False, content="bad", error_code="INPUT_INVALID", recoverable=True)
            for _ in range(5)
        ]
    )
    use_model(monkeypatch, model)
    use_tool(monkeypatch, tool_stub)

    body = await _invoke(api_client, admin_headers, agent["id"])
    assert body["status"] == "FAILED"
    assert body["error"]["code"] == "BUDGET_EXCEEDED"
    assert body["error"]["details"]["limit"] == "STEP_LIMIT"
    assert body["error"]["details"]["used_steps"] >= 3
    assert len(model.requests) <= 3
    assert len(tool_stub.calls) <= 2


# ---------------------------------------------------------------- 运行时上下文


async def test_runtime_context_reaches_model_and_stays_out_of_history(
    api_client,
    tenant_env: TenantEnv,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = CapturingModel([_answer("第一轮"), _answer("第二轮")])
    use_model(monkeypatch, model)
    prompt = await _create_qa_prompt_version(api_client, admin_headers)
    agent, _ = await _published_agent(
        api_client,
        admin_headers,
        prompt_version=prompt,
        runtime_context=["now", "timezone"],
    )

    await _invoke(api_client, admin_headers, agent["id"], session_id="topic-a", input={"q": "1"})
    await _invoke(api_client, admin_headers, agent["id"], session_id="topic-a", input={"q": "2"})

    first, second = model.requests
    context_message = first.messages[1]
    assert first.messages[0].role == "system"
    assert context_message.role == "system"
    assert "当前时间：" in context_message.content
    assert re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", context_message.content)
    assert "时区：" in context_message.content

    # 第二轮仍然只有一条上下文消息：上一轮的上下文没有进入历史
    assert [item.role for item in second.messages] == [
        "system",
        "system",
        "user",
        "assistant",
        "user",
    ]
    assert second.messages[2].content == "Q: 1"
    assert second.messages[4].content == "Q: 2"
    assert sum("当前时间：" in item.content for item in second.messages) == 1

    async with session_scope(tenant_env.tenant_id) as session:
        session_id = await session.scalar(
            text(
                "select id from execution.execution_session "
                "where tenant_id = :tid and agent_id = :aid "
                "order by created_at desc limit 1"
            ),
            {"tid": tenant_env.tenant_id, "aid": uuid.UUID(agent["id"])},
        )
    assert session_id is not None

    saver = await checkpoint_store.saver()
    found = await saver.aget_tuple({"configurable": {"thread_id": f"sess-{session_id}"}})
    assert found is not None
    stored = [
        dict(item)
        for item in (found.checkpoint.get("channel_values") or {}).get("messages") or []
    ]
    assert all("当前时间：" not in str(item.get("content")) for item in stored)


async def test_undeclared_agent_gets_no_context_message(
    api_client,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = CapturingModel([_answer()])
    use_model(monkeypatch, model)
    agent, _ = await _published_agent(api_client, admin_headers)

    await _invoke(api_client, admin_headers, agent["id"])

    assert [item.role for item in model.requests[0].messages] == ["system"]


# ---------------------------------------------------------------- 版本对比


async def test_version_diff_reports_runtime_context_declaration(
    api_client,
    admin_headers: dict[str, str],
) -> None:
    model = await create_model(api_client, admin_headers)
    prompt = await create_prompt_version(api_client, admin_headers)
    agent = await create_agent(api_client, admin_headers)
    base = await create_agent_version(
        api_client,
        admin_headers,
        agent["id"],
        model_id=model["id"],
        prompt_version_id=prompt["id"],
    )
    target = await create_agent_version(
        api_client,
        admin_headers,
        agent["id"],
        model_id=model["id"],
        prompt_version_id=prompt["id"],
        runtime_context=["now", "timezone"],
    )

    response = await api_client.get(
        "/api/v1/agent-versions/diff",
        headers=admin_headers,
        params={"base_version_id": base["id"], "target_version_id": target["id"]},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["identical"] is False
    assert body["config_hash_changed"] is True
    assert body["runtime_context"] == {
        "changed": True,
        "base": [],
        "target": ["now", "timezone"],
    }
