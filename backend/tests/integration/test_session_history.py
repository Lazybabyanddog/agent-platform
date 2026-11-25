"""调试台会话历史：列表可辨识性、归属隔离与结构化回放。"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from sqlalchemy import text

from agent_platform.contexts.execution.application.invocation_service import invocation_service
from agent_platform.contexts.execution.domain.ports import (
    ModelRequest,
    ModelResponse,
    ToolCall,
    ToolResult,
)
from agent_platform.shared.db import session_scope, system_session_scope
from tests.integration.agent_helpers import (
    create_agent,
    create_model,
    create_tool_version,
    suffix,
)
from tests.integration.conftest import TenantEnv

ADMIN_PASSWORD = "AdminPass123!"


class StaticModel:
    def __init__(self, *, content: str = "已收到") -> None:
        self.content = content

    async def chat(self, request: ModelRequest) -> ModelResponse:
        del request
        return ModelResponse(content=self.content, input_tokens=8, output_tokens=4)


class ToolCallingModel:
    def __init__(self, tool_code: str) -> None:
        self.tool_code = tool_code
        self.calls = 0

    async def chat(self, request: ModelRequest) -> ModelResponse:
        del request
        self.calls += 1
        if self.calls == 1:
            return ModelResponse(
                content="",
                tool_calls=(
                    ToolCall(id="call-1", name=self.tool_code, arguments={"text": "hi"}),
                ),
            )
        return ModelResponse(content="工具跑完了")


class EchoTool:
    async def invoke(self, *, tool_code: str, tool_version_id: str, arguments):
        del tool_code, tool_version_id
        return ToolResult(ok=True, content=json.dumps({"ok": True, "echo": arguments["text"]}))


def use_model(monkeypatch: pytest.MonkeyPatch, model: Any) -> None:
    async def factory(actor, snapshot, timeout_s):
        del actor, snapshot, timeout_s
        return model

    monkeypatch.setattr(invocation_service, "_model_factory", factory)


def use_tool(monkeypatch: pytest.MonkeyPatch, tool: Any) -> None:
    monkeypatch.setattr(invocation_service, "_tool_factory", lambda actor: tool)


async def _create_prompt_with_question(api_client, headers: dict[str, str]) -> dict:
    created = await api_client.post(
        "/api/v1/prompts",
        headers=headers,
        json={
            "code": f"history-prompt-{suffix()}",
            "name": "History QA",
            "messages": [{"role": "user", "content": "Q: {{question}}"}],
            "variables": [{"name": "question", "type": "string", "required": True}],
        },
    )
    assert created.status_code == 201, created.text
    versions = await api_client.get(
        f"/api/v1/prompts/{created.json()['id']}/versions", headers=headers
    )
    assert versions.status_code == 200, versions.text
    return versions.json()[0]


async def _agent_with_model(
    api_client, headers: dict[str, str]
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    model = await create_model(api_client, headers)
    prompt = await _create_prompt_with_question(api_client, headers)
    agent = await create_agent(api_client, headers)
    return agent, model, prompt


def _payload(
    model_id: str,
    prompt_version_id: str,
    *,
    session_key: str,
    text: str,
    tool_ids: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "env": "DEV",
        "model_id": model_id,
        "prompt_version_id": prompt_version_id,
        "tool_version_ids": tool_ids or [],
        "params": {},
        "policy": {"max_steps": 3},
        "input": {"question": text},
        "preview_session_id": session_key,
    }


async def _preview(api_client, headers: dict[str, str], agent_id: str, payload: dict) -> dict:
    response = await api_client.post(
        f"/api/v1/agents/{agent_id}/preview", headers=headers, json=payload
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _sessions(
    api_client, headers: dict[str, str], agent_id: str, **params: Any
) -> dict[str, Any]:
    response = await api_client.get(
        "/api/v1/sessions", headers=headers, params={"agent_id": agent_id, **params}
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _history(
    api_client, headers: dict[str, str], session_id: str, **params: Any
):
    return await api_client.get(
        f"/api/v1/sessions/{session_id}/history", headers=headers, params=params
    )


async def _run_count(tenant_id: uuid.UUID) -> int:
    async with session_scope(tenant_id) as session:
        return int(await session.scalar(text("select count(*) from execution.run")) or 0)


async def _register_member(
    api_client, tenant_env: TenantEnv
) -> tuple[dict[str, str], uuid.UUID]:
    email = f"history-member-{uuid.uuid4().hex[:8]}@example.com"
    password = "MemberPass123!"
    registered = await api_client.post(
        "/api/v1/auth/register",
        json={
            "tenant_code": tenant_env.tenant_code,
            "email": email,
            "password": password,
            "display_name": "History Member",
        },
    )
    assert registered.status_code == 201, registered.text
    login = await api_client.post(
        "/api/v1/auth/login",
        json={"tenant_code": tenant_env.tenant_code, "email": email, "password": password},
    )
    assert login.status_code == 200, login.text
    return (
        {"Authorization": f"Bearer {login.json()['access_token']}"},
        registered.json()["user_id"],
    )


async def _purge_member(user_id: uuid.UUID) -> None:
    async with system_session_scope() as session:
        await session.execute(
            text("delete from platform.membership where user_id = :uid"), {"uid": user_id}
        )
        await session.execute(
            text("delete from platform.user_role where user_id = :uid"), {"uid": user_id}
        )
        await session.execute(
            text("delete from platform.user_account where id = :uid"), {"uid": user_id}
        )


async def test_preview_session_records_title_turns_and_history(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str], monkeypatch
) -> None:
    use_model(monkeypatch, StaticModel(content="先确认订单状态"))
    agent, model, prompt = await _agent_with_model(api_client, admin_headers)
    key = f"hist-{suffix()}"
    for text_value in ("帮我梳理退款流程", "第二步怎么做"):
        await _preview(
            api_client,
            admin_headers,
            agent["id"],
            _payload(model["id"], prompt["id"], session_key=key, text=text_value),
        )

    listed = await _sessions(api_client, admin_headers, agent["id"])
    assert listed["total"] == 1
    item = listed["items"][0]
    assert item["session_key"] == key
    assert item["title"] == "帮我梳理退款流程"
    assert item["turn_count"] == 2
    assert item["owned"] is True
    assert item["expired"] is False
    assert item["preview_config_hash"]

    before = await _run_count(tenant_env.tenant_id)
    response = await _history(api_client, admin_headers, item["id"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert [turn["index"] for turn in body["turns"]] == [1, 2]
    assert body["turns"][0]["user"]["content"] == "Q: 帮我梳理退款流程"
    assert body["turns"][0]["user"]["length"] == len("Q: 帮我梳理退款流程")
    assert body["turns"][0]["assistant"]["content"] == "先确认订单状态"
    assert body["turns"][0]["run_id"]
    assert body["turns"][0]["trace_id"]
    assert body["turns"][0]["status"] == "SUCCESS"
    assert body["turns"][1]["user"]["content"] == "Q: 第二步怎么做"
    assert body["truncated"] is False
    # 回放不产生运行
    assert await _run_count(tenant_env.tenant_id) == before

    # 环境过滤下推到查询
    assert (await _sessions(api_client, admin_headers, agent["id"], env="PROD"))["total"] == 0
    assert (
        await _sessions(
            api_client, admin_headers, agent["id"], env="DEV", include_expired="false"
        )
    )["total"] == 1


async def test_history_carries_tool_entries(
    api_client, admin_headers: dict[str, str], monkeypatch
) -> None:
    tool = await create_tool_version(api_client, admin_headers)
    use_model(monkeypatch, ToolCallingModel(tool["tool_code"]))
    use_tool(monkeypatch, EchoTool())
    agent, model, prompt = await _agent_with_model(api_client, admin_headers)

    await _preview(
        api_client,
        admin_headers,
        agent["id"],
        _payload(
            model["id"],
            prompt["id"],
            session_key=f"tool-{suffix()}",
            text="查一下订单",
            tool_ids=[tool["id"]],
        ),
    )

    item = (await _sessions(api_client, admin_headers, agent["id"]))["items"][0]
    body = (await _history(api_client, admin_headers, item["id"])).json()
    turn = body["turns"][0]
    assert turn["user"]["content"] == "Q: 查一下订单"
    assert turn["assistant"]["content"] == "工具跑完了"
    assert [entry["name"] for entry in turn["tools"]] == [tool["tool_code"]]
    assert turn["tools"][0]["status"] == "SUCCESS"
    assert turn["tools"][0]["latency_ms"] is not None


async def test_history_is_scoped_to_creator(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str], monkeypatch
) -> None:
    use_model(monkeypatch, StaticModel())
    agent, model, prompt = await _agent_with_model(api_client, admin_headers)
    member_headers, member_id = await _register_member(api_client, tenant_env)
    try:
        await _preview(
            api_client,
            admin_headers,
            agent["id"],
            _payload(model["id"], prompt["id"], session_key=f"adm-{suffix()}", text="管理员的"),
        )
        await _preview(
            api_client,
            member_headers,
            agent["id"],
            _payload(model["id"], prompt["id"], session_key=f"mem-{suffix()}", text="成员的"),
        )

        mine = await _sessions(api_client, admin_headers, agent["id"])
        assert mine["total"] == 1
        assert mine["items"][0]["title"] == "管理员的"

        everyone = await _sessions(
            api_client, admin_headers, agent["id"], mine_only="false"
        )
        assert everyone["total"] == 2
        assert {item["owned"] for item in everyone["items"]} == {True, False}

        member_view = await _sessions(api_client, member_headers, agent["id"])
        assert member_view["total"] == 1
        assert member_view["items"][0]["title"] == "成员的"

        member_history = await _history(
            api_client, member_headers, member_view["items"][0]["id"]
        )
        assert member_history.status_code == 200, member_history.text
        assert member_history.json()["turns"][0]["user"]["content"] == "Q: 成员的"

        others = [
            item for item in everyone["items"] if item["owned"] is False
        ][0]
        hidden = await _history(api_client, admin_headers, others["id"])
        assert hidden.status_code == 404, hidden.text

        own = [item for item in everyone["items"] if item["owned"] is True][0]
        reverse = await _history(api_client, member_headers, own["id"])
        assert reverse.status_code == 404, reverse.text
    finally:
        await _purge_member(member_id)


async def test_expired_session_stays_readable_until_key_reused(
    api_client, admin_headers: dict[str, str], monkeypatch
) -> None:
    use_model(monkeypatch, StaticModel(content="过期前的回答"))
    agent, model, prompt = await _agent_with_model(api_client, admin_headers)
    key = f"exp-{suffix()}"
    await _preview(
        api_client,
        admin_headers,
        agent["id"],
        _payload(model["id"], prompt["id"], session_key=key, text="过期前的问题"),
    )
    old = (await _sessions(api_client, admin_headers, agent["id"]))["items"][0]

    async with system_session_scope() as session:
        await session.execute(
            text(
                "update execution.execution_session set expires_at = now() - interval '1 hour' "
                "where id = :sid"
            ),
            {"sid": str(old["id"])},
        )

    listed = await _sessions(api_client, admin_headers, agent["id"])
    assert listed["total"] == 1
    assert listed["items"][0]["expired"] is True
    history = await _history(api_client, admin_headers, old["id"])
    assert history.status_code == 200, history.text
    assert history.json()["turns"][0]["user"]["content"] == "Q: 过期前的问题"

    # 同标识再次受理：按新会话处理，旧行被回收
    await _preview(
        api_client,
        admin_headers,
        agent["id"],
        _payload(model["id"], prompt["id"], session_key=key, text="过期后的问题"),
    )
    after = await _sessions(api_client, admin_headers, agent["id"])
    assert after["total"] == 1
    assert after["items"][0]["id"] != old["id"]
    assert after["items"][0]["turn_count"] == 1
    assert (await _history(api_client, admin_headers, old["id"])).status_code == 404


async def test_history_turns_limit_truncates_from_tail(
    api_client, admin_headers: dict[str, str], monkeypatch
) -> None:
    use_model(monkeypatch, StaticModel())
    agent, model, prompt = await _agent_with_model(api_client, admin_headers)
    key = f"limit-{suffix()}"
    for index in range(3):
        await _preview(
            api_client,
            admin_headers,
            agent["id"],
            _payload(model["id"], prompt["id"], session_key=key, text=f"第 {index} 问"),
        )

    listed = await _sessions(api_client, admin_headers, agent["id"])
    session_id = listed["items"][0]["id"]
    body = (await _history(api_client, admin_headers, session_id, turns_limit=2)).json()
    assert body["truncated"] is True
    assert [turn["index"] for turn in body["turns"]] == [2, 3]
    assert body["turns"][0]["user"]["content"] == "Q: 第 1 问"


async def _drop_session_thread(session_id: str) -> None:
    """模拟没有 user 消息锚点的存量会话：清掉该 thread 的 checkpoint。"""
    thread_id = f"sess-{session_id}"
    async with system_session_scope() as session:
        for statement in (
            "delete from agent.checkpoint_writes where thread_id = :tid",
            "delete from agent.checkpoint_blobs where thread_id = :tid",
            "delete from agent.checkpoints where thread_id = :tid",
        ):
            await session.execute(text(statement), {"tid": thread_id})


async def test_history_rebuilds_turns_from_runs_without_user_anchor(
    api_client, admin_headers: dict[str, str], monkeypatch
) -> None:
    use_model(monkeypatch, StaticModel(content="先确认订单状态"))
    agent, model, prompt = await _agent_with_model(api_client, admin_headers)
    key = f"anchor-{suffix()}"
    await _preview(
        api_client,
        admin_headers,
        agent["id"],
        _payload(
            model["id"],
            prompt["id"],
            session_key=key,
            text="帮我梳理退款流程",
        ),
    )

    item = (await _sessions(api_client, admin_headers, agent["id"]))["items"][0]
    # 新会话由运行时写入 user 消息，属于正常回放，不走重建
    fresh = (await _history(api_client, admin_headers, item["id"])).json()
    assert fresh["reconstructed"] is False
    assert fresh["turns"][0]["user"]["content"] == "Q: 帮我梳理退款流程"

    await _drop_session_thread(item["id"])
    rebuilt = (await _history(api_client, admin_headers, item["id"])).json()
    assert rebuilt["reconstructed"] is True
    assert len(rebuilt["turns"]) == item["turn_count"] == 1
    turn = rebuilt["turns"][0]
    # 用户原话来自 run.input（单变量取原文），回答缺失时如实留空
    assert turn["user"]["content"] == "帮我梳理退款流程"
    assert turn["user"]["length"] == len("帮我梳理退款流程")
    assert turn["assistant"] is None
    assert turn["run_id"]
    assert turn["status"] == "SUCCESS"

    # 继续聊之后，新线程只锚定最近一轮；早期轮次仍由运行记录补齐
    await _preview(
        api_client,
        admin_headers,
        agent["id"],
        _payload(model["id"], prompt["id"], session_key=key, text="追问一句"),
    )
    mixed = (await _history(api_client, admin_headers, item["id"])).json()
    assert mixed["reconstructed"] is True
    assert mixed["session"]["turn_count"] == 2
    # 有锚点的最近一轮按线程原文回放，早期轮次用运行输入补齐
    assert [turn["user"]["content"] for turn in mixed["turns"]] == [
        "帮我梳理退款流程",
        "Q: 追问一句",
    ]
    assert mixed["turns"][0]["assistant"] is None
    assert mixed["turns"][1]["assistant"]["content"] == "先确认订单状态"


async def test_history_requires_agent_read(
    api_client, admin_headers: dict[str, str], monkeypatch
) -> None:
    use_model(monkeypatch, StaticModel())
    agent, model, prompt = await _agent_with_model(api_client, admin_headers)
    await _preview(
        api_client,
        admin_headers,
        agent["id"],
        _payload(model["id"], prompt["id"], session_key=f"perm-{suffix()}", text="权限"),
    )
    session_id = (await _sessions(api_client, admin_headers, agent["id"]))["items"][0]["id"]

    created = await api_client.post(
        "/api/v1/api-keys",
        headers=admin_headers,
        json={"name": "reader", "scopes": ["run:read"]},
    )
    assert created.status_code == 201, created.text
    reader = {"Authorization": f"ApiKey {created.json()['secret']}"}

    # 历史需要 AGENT_READ
    forbidden = await _history(api_client, reader, session_id)
    assert forbidden.status_code == 403, forbidden.text
    assert forbidden.json()["error"]["code"] == "FORBIDDEN"


async def test_unknown_session_history_is_not_found(
    api_client, admin_headers: dict[str, str]
) -> None:
    response = await _history(api_client, admin_headers, str(uuid.uuid4()))
    assert response.status_code == 404, response.text
