"""调试台验收：多会话列表 → 回放 → 续聊沿用首轮绑定。"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text

from agent_platform.shared.db import session_scope
from tests.integration.agent_helpers import create_agent, create_model, suffix
from tests.integration.conftest import TenantEnv
from tests.integration.test_agent_invoke import StaticModel, use_model

CONTENT = "固定回答"


async def _create_question_prompt(api_client, headers: dict[str, str]) -> dict[str, Any]:
    created = await api_client.post(
        "/api/v1/prompts",
        headers=headers,
        json={
            "code": f"playground-prompt-{suffix()}",
            "name": "Playground QA",
            "messages": [{"role": "user", "content": "Q: {{question}}"}],
            "variables": [{"name": "question", "type": "string", "required": True}],
        },
    )
    assert created.status_code == 201, created.text
    versions = await api_client.get(f"/api/v1/prompts/{created.json()['id']}/versions", headers=headers)
    assert versions.status_code == 200, versions.text
    return versions.json()[0]


async def _create_system_prompt(api_client, headers: dict[str, str]) -> dict[str, Any]:
    """单条 system 的 Prompt：输入没有占位符，靠运行时补成用户消息。"""
    created = await api_client.post(
        "/api/v1/prompts",
        headers=headers,
        json={
            "code": f"playground-system-{suffix()}",
            "name": "System Only",
            "messages": [{"role": "system", "content": "你是客服助手，直接回答用户问题。"}],
            "variables": [{"name": "question", "type": "string", "required": True}],
        },
    )
    assert created.status_code == 201, created.text
    versions = await api_client.get(
        f"/api/v1/prompts/{created.json()['id']}/versions", headers=headers
    )
    assert versions.status_code == 200, versions.text
    return versions.json()[0]


async def _agent(api_client, headers: dict[str, str]) -> tuple[dict, dict, dict]:
    model = await create_model(api_client, headers)
    prompt = await _create_question_prompt(api_client, headers)
    agent = await create_agent(api_client, headers)
    return agent, model, prompt


def _payload(
    model_id: str, prompt_version_id: str, *, session_key: str, text: str, temperature: float
) -> dict[str, Any]:
    return {
        "env": "DEV",
        "model_id": model_id,
        "prompt_version_id": prompt_version_id,
        "tool_version_ids": [],
        "params": {"temperature": temperature},
        "policy": {"max_steps": 3},
        "input": {"question": text},
        "preview_session_id": session_key,
    }


async def _preview(api_client, headers, agent_id: str, payload: dict) -> dict:
    response = await api_client.post(f"/api/v1/agents/{agent_id}/preview", headers=headers, json=payload)
    assert response.status_code == 200, response.text
    return response.json()


async def _sessions(api_client, headers, agent_id: str) -> dict:
    response = await api_client.get("/api/v1/sessions", headers=headers, params={"agent_id": agent_id})
    assert response.status_code == 200, response.text
    return response.json()


async def _run_count(tenant_id: uuid.UUID) -> int:
    async with session_scope(tenant_id) as session:
        return int(await session.scalar(text("select count(*) from execution.run")) or 0)


async def test_playground_replay_then_continue_keeps_binding(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str], monkeypatch
) -> None:
    model = StaticModel(content=CONTENT)
    use_model(monkeypatch, model)
    agent, model_row, prompt = await _agent(api_client, admin_headers)

    keys = [f"pg-{name}-{suffix()}" for name in ("a", "b", "c", "d")]
    first = await _preview(
        api_client,
        admin_headers,
        agent["id"],
        _payload(model_row["id"], prompt["id"], session_key=keys[0], text="第一条", temperature=0.2),
    )
    # 另外三个会话：模拟调试台连续新建
    for index, key in enumerate(keys[1:], start=2):
        await _preview(
            api_client,
            admin_headers,
            agent["id"],
            _payload(model_row["id"], prompt["id"], session_key=key, text=f"第 {index} 条", temperature=0.2),
        )

    listed = await _sessions(api_client, admin_headers, agent["id"])
    assert listed["total"] == 4
    assert {item["turn_count"] for item in listed["items"]} == {1}
    first_item = next(item for item in listed["items"] if item["session_key"] == keys[0])
    assert first_item["preview_config_hash"] == first["config_hash"]

    # 切回第一条回放：不产生运行、绑定与轮数不变
    before_replay = await _run_count(tenant_env.tenant_id)
    history = await api_client.get(f"/api/v1/sessions/{first_item['id']}/history", headers=admin_headers)
    assert history.status_code == 200, history.text
    body = history.json()
    assert [turn["user"]["content"] for turn in body["turns"]] == ["Q: 第一条"]
    assert body["turns"][0]["assistant"]["content"] == CONTENT
    assert body["session"]["turn_count"] == 1
    assert await _run_count(tenant_env.tenant_id) == before_replay

    # 续聊：沿用首轮快照，即使本次请求带了不同的编辑态参数
    calls_before = len(model.requests)
    continued = await _preview(
        api_client,
        admin_headers,
        agent["id"],
        _payload(model_row["id"], prompt["id"], session_key=keys[0], text="继续问", temperature=1.5),
    )
    assert continued["config_hash"] == first["config_hash"]
    assert len(model.requests) == calls_before + 1
    assert CONTENT in [message.content for message in model.requests[calls_before].messages]
    assert await _run_count(tenant_env.tenant_id) == before_replay + 1

    replayed = (
        await api_client.get(f"/api/v1/sessions/{first_item['id']}/history", headers=admin_headers)
    ).json()
    assert [turn["index"] for turn in replayed["turns"]] == [1, 2]
    assert replayed["turns"][1]["user"]["content"] == "Q: 继续问"
    assert replayed["turns"][1]["assistant"]["content"] == CONTENT

    refreshed = await _sessions(api_client, admin_headers, agent["id"])
    assert refreshed["total"] == 4
    refreshed_item = next(item for item in refreshed["items"] if item["session_key"] == keys[0])
    assert refreshed_item["id"] == first_item["id"]
    assert refreshed_item["turn_count"] == 2
    assert refreshed_item["preview_config_hash"] == first["config_hash"]


async def test_system_only_preview_feeds_input_and_replays_after_refresh(
    api_client, admin_headers: dict[str, str], monkeypatch
) -> None:
    """模板只有 system 时，本次输入仍要进入上下文，并能回放与续聊。"""
    model = StaticModel(content=CONTENT)
    use_model(monkeypatch, model)
    model_row = await create_model(api_client, admin_headers)
    prompt = await _create_system_prompt(api_client, admin_headers)
    agent = await create_agent(api_client, admin_headers)
    key = f"pg-system-{suffix()}"

    calls_before = len(model.requests)
    first = await _preview(
        api_client,
        admin_headers,
        agent["id"],
        _payload(model_row["id"], prompt["id"], session_key=key, text="我的订单到哪了", temperature=0.2),
    )
    assert first["output"] == CONTENT
    # 模型实际收到的消息里必须有本次输入
    sent = model.requests[calls_before].messages
    assert [message.role for message in sent] == ["system", "user"]
    assert sent[-1].content == "我的订单到哪了"

    # 刷新页面：按会话重新读历史即可回放
    listed = await _sessions(api_client, admin_headers, agent["id"])
    item = listed["items"][0]
    assert item["turn_count"] == 1
    history = await api_client.get(
        f"/api/v1/sessions/{item['id']}/history", headers=admin_headers
    )
    assert history.status_code == 200, history.text
    body = history.json()
    assert body["reconstructed"] is False
    assert [turn["user"]["content"] for turn in body["turns"]] == ["我的订单到哪了"]
    assert body["turns"][0]["assistant"]["content"] == CONTENT

    # 续聊沿用同一会话标识：模型能看到上一轮输入与回答
    calls_before = len(model.requests)
    await _preview(
        api_client,
        admin_headers,
        agent["id"],
        _payload(model_row["id"], prompt["id"], session_key=key, text="大概几天到", temperature=0.2),
    )
    continued = model.requests[calls_before].messages
    assert [message.role for message in continued] == ["system", "user", "assistant", "user"]
    assert "我的订单到哪了" in [message.content for message in continued]
    assert continued[-1].content == "大概几天到"

    after = (
        await api_client.get(
            f"/api/v1/sessions/{item['id']}/history", headers=admin_headers
        )
    ).json()
    assert [turn["index"] for turn in after["turns"]] == [1, 2]
    assert after["turns"][1]["user"]["content"] == "大概几天到"
    assert after["session"]["turn_count"] == 2
