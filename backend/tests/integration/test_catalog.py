"""catalog 上下文：模型 CRUD、参数校验接口、连通性探测、Prompt 版本。"""

from __future__ import annotations

import uuid

import httpx

from agent_platform.contexts.catalog.application.model_service import (
    PROBE_AUTH_FAILED,
    PROBE_OK,
    ModelService,
)
from agent_platform.contexts.catalog.domain.enums import RiskLevel, ToolStatus, ToolType
from agent_platform.contexts.platform.application.auth_service import auth_service
from tests.integration.conftest import TenantEnv

PARAM_TEMPLATE = {
    "temperature": {"type": "number", "min": 0, "max": 2, "default": 0.2},
    "max_tokens": {"type": "integer", "min": 1, "max": 32000, "default": 1024},
}


async def _create_provider(api_client, headers: dict[str, str], name: str = "stub") -> str:
    response = await api_client.post(
        "/api/v1/model-providers",
        headers=headers,
        json={"name": name, "base_url": "http://127.0.0.1:9/v1"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _create_model(api_client, headers: dict[str, str], code: str) -> dict:
    provider_id = await _create_provider(api_client, headers)
    response = await api_client.post(
        "/api/v1/models",
        headers=headers,
        json={
            "provider_id": provider_id,
            "code": code,
            "model_name": "gpt-4o-mini",
            "ctx_window": 128000,
            "param_template": PARAM_TEMPLATE,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_model_code_is_unique_and_template_is_validated(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str]
) -> None:
    model = await _create_model(api_client, admin_headers, "chat")
    assert model["param_template"]["temperature"]["max"] == 2
    assert model["status"] == "ACTIVE"

    duplicated = await api_client.post(
        "/api/v1/models",
        headers=admin_headers,
        json={
            "provider_id": model["provider_id"],
            "code": "chat",
            "model_name": "another",
        },
    )
    assert duplicated.status_code == 409

    bad_template = await api_client.post(
        "/api/v1/models",
        headers=admin_headers,
        json={
            "provider_id": model["provider_id"],
            "code": "broken",
            "model_name": "x",
            "param_template": {"temperature": {"type": "number", "min": 2, "max": 1}},
        },
    )
    assert bad_template.status_code == 422
    assert bad_template.json()["error"]["details"][0]["field"] == "param_template.temperature"

    disabled = await api_client.patch(
        f"/api/v1/models/{model['id']}/status", headers=admin_headers, json={"status": "DISABLED"}
    )
    assert disabled.status_code == 200
    assert disabled.json()["status"] == "DISABLED"


async def test_param_values_are_range_checked_with_field_location(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str]
) -> None:
    model = await _create_model(api_client, admin_headers, "chat2")

    out_of_range = await api_client.post(
        f"/api/v1/models/{model['id']}/validate-params",
        headers=admin_headers,
        json={"params": {"temperature": 5}},
    )
    assert out_of_range.status_code == 422
    assert out_of_range.json()["error"]["details"][0]["field"] == "params.temperature"

    unknown = await api_client.post(
        f"/api/v1/models/{model['id']}/validate-params",
        headers=admin_headers,
        json={"params": {"presence_penalty": 1}},
    )
    assert unknown.status_code == 422
    assert unknown.json()["error"]["details"][0]["field"] == "params.presence_penalty"

    ok = await api_client.post(
        f"/api/v1/models/{model['id']}/validate-params",
        headers=admin_headers,
        json={"params": {"temperature": 1.5}},
    )
    assert ok.status_code == 200
    assert ok.json()["params"] == {"temperature": 1.5, "max_tokens": 1024}


async def test_probe_classifies_upstream_failures(tenant_env: TenantEnv) -> None:
    actor = await auth_service.build_actor(
        tenant_id=tenant_env.tenant_id, user_id=tenant_env.user_id
    )

    async def ok_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/models")
        return httpx.Response(200, json={"data": [{"id": "a"}, {"id": "gpt-4o-mini"}]})

    async def unauthorized_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "bad key"})

    service = ModelService(transport=httpx.MockTransport(ok_handler))
    provider = await service.create_provider(
        actor=actor, name="ok-provider", base_url="https://stub.local/v1"
    )
    model = await service.create_model(
        actor=actor,
        provider_id=provider.id,
        code=f"probe-{uuid.uuid4().hex[:6]}",
        model_name="gpt-4o-mini",
    )
    result = await service.probe(actor=actor, model_id=model.id)
    assert result.ok is True
    assert result.error_code == PROBE_OK
    assert result.model_count == 2

    failing = ModelService(transport=httpx.MockTransport(unauthorized_handler))
    bad_result = await failing.probe(actor=actor, model_id=model.id)
    assert bad_result.ok is False
    assert bad_result.error_code == PROBE_AUTH_FAILED
    assert bad_result.status_code == 401


async def test_probe_never_exposes_credentials(tenant_env: TenantEnv) -> None:
    from agent_platform.contexts.platform.application.secret_service import secret_service

    actor = await auth_service.build_actor(
        tenant_id=tenant_env.tenant_id, user_id=tenant_env.user_id
    )
    secret = await secret_service.create(
        actor=actor, name=f"probe-{uuid.uuid4().hex[:6]}", value="sk-probe-secret"
    )
    captured: dict[str, str] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={"data": []})

    service = ModelService(transport=httpx.MockTransport(handler))
    provider = await service.create_provider(
        actor=actor, name="with-secret", base_url="https://stub.local/v1", secret_ref_id=secret.id
    )
    model = await service.create_model(
        actor=actor,
        provider_id=provider.id,
        code=f"probe-secret-{uuid.uuid4().hex[:6]}",
        model_name="gpt-4o-mini",
    )
    result = await service.probe(actor=actor, model_id=model.id)
    assert captured["authorization"] == "Bearer sk-probe-secret"
    assert "sk-probe-secret" not in repr(result)
    assert "sk-probe-secret" not in str(vars(result) if hasattr(result, "__dict__") else result)


async def test_prompt_versions_are_immutable_and_diffable(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str]
) -> None:
    created = await api_client.post(
        "/api/v1/prompts",
        headers=admin_headers,
        json={
            "code": "support",
            "name": "Support",
            "messages": [{"role": "system", "content": "You are {{persona}}."}],
            "variables": [{"name": "persona", "type": "string", "required": True}],
        },
    )
    assert created.status_code == 201, created.text
    prompt_id = created.json()["id"]

    versions = await api_client.get(f"/api/v1/prompts/{prompt_id}/versions", headers=admin_headers)
    assert [v["version_no"] for v in versions.json()] == [1]
    first = versions.json()[0]

    second = await api_client.post(
        f"/api/v1/prompts/{prompt_id}/versions",
        headers=admin_headers,
        json={
            "messages": [{"role": "system", "content": "You are {{persona}}, be brief."}],
            "variables": [{"name": "persona", "type": "string", "required": True}],
            "note": "shorter",
        },
    )
    assert second.status_code == 201, second.text
    assert second.json()["version_no"] == 2
    assert second.json()["config_hash"] != first["config_hash"]

    diff = await api_client.get(
        "/api/v1/prompt-versions/diff",
        headers=admin_headers,
        params={"base": first["id"], "target": second.json()["id"]},
    )
    assert diff.status_code == 200, diff.text
    assert diff.json()["identical"] is False
    assert diff.json()["message_changes"][0]["index"] == 0

    rendered = await api_client.post(
        f"/api/v1/prompt-versions/{second.json()['id']}/render",
        headers=admin_headers,
        json={"values": {"persona": "helper"}},
    )
    assert rendered.status_code == 200
    assert rendered.json()["messages"][0]["content"] == "You are helper, be brief."

    # 版本没有修改入口
    put = await api_client.put(
        f"/api/v1/prompt-versions/{second.json()['id']}", headers=admin_headers, json={}
    )
    assert put.status_code in {404, 405}

    missing = await api_client.post(
        f"/api/v1/prompt-versions/{second.json()['id']}/render",
        headers=admin_headers,
        json={"values": {}},
    )
    assert missing.status_code == 422
    assert missing.json()["error"]["details"][0]["field"] == "variables.persona"


async def test_prompt_version_number_unique_per_prompt(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str]
) -> None:
    from sqlalchemy import func, select
    from sqlalchemy.exc import IntegrityError

    from agent_platform.contexts.catalog.infrastructure.repositories import PromptVersionRepository
    from agent_platform.contexts.catalog.infrastructure.tables import PromptVersion
    from agent_platform.shared.db import session_scope
    from agent_platform.shared.hashing import config_hash

    created = await api_client.post(
        "/api/v1/prompts",
        headers=admin_headers,
        json={
            "code": f"uniq-{uuid.uuid4().hex[:6]}",
            "name": "Uniq",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    prompt_id = uuid.UUID(created.json()["id"])

    try:
        async with session_scope(tenant_env.tenant_id) as session:
            await PromptVersionRepository().create(
                session,
                tenant_id=tenant_env.tenant_id,
                prompt_id=prompt_id,
                version_no=1,
                messages=[{"role": "user", "content": "concurrent"}],
                variables=[],
                config_hash=config_hash({"v": 2}),
                note=None,
                created_by=None,
            )
    except IntegrityError:
        pass
    else:  # pragma: no cover - 只有唯一约束缺失时才会走到这里
        raise AssertionError("duplicate version_no must be rejected by the database")

    async with session_scope(tenant_env.tenant_id) as session:
        total = await session.scalar(
            select(func.count()).select_from(PromptVersion).where(PromptVersion.prompt_id == prompt_id)
        )
    assert total == 1


async def test_prompt_duplicate_code_is_conflict(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str]
) -> None:
    payload = {"code": f"p-{uuid.uuid4().hex[:6]}", "name": "P"}
    first = await api_client.post("/api/v1/prompts", headers=admin_headers, json=payload)
    assert first.status_code == 201
    second = await api_client.post("/api/v1/prompts", headers=admin_headers, json=payload)
    assert second.status_code == 409


async def test_prompt_delete_cascades_versions_and_frees_code(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str]
) -> None:
    from sqlalchemy import func, select

    from agent_platform.contexts.catalog.infrastructure.tables import PromptVersion
    from agent_platform.shared.db import session_scope

    code = f"del-{uuid.uuid4().hex[:6]}"
    created = await api_client.post(
        "/api/v1/prompts",
        headers=admin_headers,
        json={
            "code": code,
            "name": "Deletable",
            "messages": [{"role": "system", "content": "bye"}],
        },
    )
    assert created.status_code == 201, created.text
    prompt_id = uuid.UUID(created.json()["id"])

    deleted = await api_client.delete(f"/api/v1/prompts/{prompt_id}", headers=admin_headers)
    assert deleted.status_code == 204, deleted.text

    gone = await api_client.get(f"/api/v1/prompts/{prompt_id}", headers=admin_headers)
    assert gone.status_code == 404
    again = await api_client.delete(f"/api/v1/prompts/{prompt_id}", headers=admin_headers)
    assert again.status_code == 404

    async with session_scope(tenant_env.tenant_id) as session:
        remaining = await session.scalar(
            select(func.count()).select_from(PromptVersion).where(PromptVersion.prompt_id == prompt_id)
        )
    assert remaining == 0

    # 编码随删除释放，可以重新创建
    recreated = await api_client.post(
        "/api/v1/prompts", headers=admin_headers, json={"code": code, "name": "Again"}
    )
    assert recreated.status_code == 201, recreated.text


async def test_prompt_delete_blocked_while_referenced_by_agent_version(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str]
) -> None:
    from agent_platform.contexts.agent.domain.enums import (
        AgentStatus,
        AgentType,
        BindingResourceType,
        BindingRole,
    )
    from agent_platform.contexts.agent.infrastructure.tables import (
        Agent,
        AgentVersion,
        AgentVersionBinding,
    )
    from agent_platform.shared.db import session_scope

    created = await api_client.post(
        "/api/v1/prompts",
        headers=admin_headers,
        json={
            "code": f"used-{uuid.uuid4().hex[:6]}",
            "name": "Used",
            "messages": [{"role": "system", "content": "hi"}],
        },
    )
    assert created.status_code == 201, created.text
    prompt_id = uuid.UUID(created.json()["id"])

    async with session_scope(tenant_env.tenant_id) as session:
        agent = Agent(
            tenant_id=tenant_env.tenant_id,
            code=f"a-{uuid.uuid4().hex[:6]}",
            name="Referrer",
            type=AgentType.CHAT,
            tags=[],
            status=AgentStatus.DRAFT,
        )
        session.add(agent)
        await session.flush()
        version = AgentVersion(
            tenant_id=tenant_env.tenant_id,
            agent_id=agent.id,
            version_no=1,
            snapshot={},
            config_hash="0" * 64,
        )
        session.add(version)
        await session.flush()
        session.add(
            AgentVersionBinding(
                tenant_id=tenant_env.tenant_id,
                agent_version_id=version.id,
                role=BindingRole.PROMPT,
                resource_type=BindingResourceType.PROMPT_VERSION,
                resource_id=prompt_id,
                overrides={},
            )
        )

    blocked = await api_client.delete(f"/api/v1/prompts/{prompt_id}", headers=admin_headers)
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["error"]["code"] == "PROMPT_IN_USE"
    assert blocked.json()["error"]["details"][0]["agent_version_count"] == 1

    still_there = await api_client.get(f"/api/v1/prompts/{prompt_id}", headers=admin_headers)
    assert still_there.status_code == 200


# ------------------------------------------------------------------ 工具契约聚合


def _tool_spec(url: str = "https://tools.local/echo") -> dict:
    return {
        "method": "POST",
        "url": url,
        "timeout_s": 5,
        "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}},
    }


async def _create_tool(
    api_client,
    headers: dict[str, str],
    *,
    code: str | None = None,
    risk_level: str = "READ_ONLY",
    spec: dict | None = None,
) -> dict:
    response = await api_client.post(
        "/api/v1/tools",
        headers=headers,
        json={
            "code": code or f"tool-{uuid.uuid4().hex[:8]}",
            "name": "Echo",
            "description": "echo service",
            "spec": spec or _tool_spec(),
            "risk_level": risk_level,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_latest_for_tools_returns_highest_version_per_tool(tenant_env: TenantEnv) -> None:
    from agent_platform.contexts.catalog.application.tool_service import ToolService
    from agent_platform.contexts.catalog.infrastructure.repositories import ToolVersionRepository
    from agent_platform.shared.db import session_scope

    actor = await auth_service.build_actor(
        tenant_id=tenant_env.tenant_id, user_id=tenant_env.user_id
    )
    service = ToolService()
    first, _ = await service.create(
        actor=actor,
        code=f"multi-{uuid.uuid4().hex[:6]}",
        name="Multi",
        description="multi tool",
        spec=_tool_spec(),
        risk_level=RiskLevel.READ_ONLY,
    )
    await service.add_version(
        actor=actor, tool_id=first.id, spec=_tool_spec(), risk_level=RiskLevel.WRITE
    )
    second, _ = await service.create(
        actor=actor,
        code=f"single-{uuid.uuid4().hex[:6]}",
        name="Single",
        description="single tool",
        spec=_tool_spec(),
        risk_level=RiskLevel.READ_ONLY,
    )

    async with session_scope(tenant_env.tenant_id) as session:
        latest = await ToolVersionRepository().latest_for_tools(
            session, tool_ids=[first.id, second.id]
        )

    assert set(latest) == {first.id, second.id}
    assert latest[first.id].version_no == 2
    assert latest[first.id].risk_level == RiskLevel.WRITE
    assert latest[second.id].version_no == 1


async def test_tool_list_and_detail_expose_aggregate_fields(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str]
) -> None:
    from datetime import datetime

    tool = await _create_tool(api_client, admin_headers)
    version = await api_client.post(
        f"/api/v1/tools/{tool['id']}/versions",
        headers=admin_headers,
        json={"spec": _tool_spec("https://tools.local/v2"), "risk_level": "WRITE"},
    )
    assert version.status_code == 201, version.text

    listed = await api_client.get("/api/v1/tools", headers=admin_headers)
    assert listed.status_code == 200
    entry = next(item for item in listed.json() if item["id"] == tool["id"])
    assert entry["risk_level"] == "WRITE"
    assert entry["latest_version_no"] == 2
    assert entry["granted"] is False
    assert datetime.fromisoformat(entry["updated_at"]) is not None

    detail = await api_client.get(f"/api/v1/tools/{tool['id']}", headers=admin_headers)
    assert detail.status_code == 200
    assert detail.json()["risk_level"] == "WRITE"
    assert detail.json()["latest_version_no"] == 2
    assert detail.json()["granted"] is False


async def test_tool_list_query_count_does_not_grow_with_tools(tenant_env: TenantEnv) -> None:
    from sqlalchemy import event

    from agent_platform.contexts.catalog.application.tool_service import ToolService
    from agent_platform.shared.db import get_engine

    actor = await auth_service.build_actor(
        tenant_id=tenant_env.tenant_id, user_id=tenant_env.user_id
    )
    service = ToolService()
    for index in range(3):
        await service.create(
            actor=actor,
            code=f"count-{index}-{uuid.uuid4().hex[:6]}",
            name=f"Count {index}",
            description=None,
            spec=_tool_spec(),
            risk_level=RiskLevel.READ_ONLY,
        )

    statements: list[str] = []

    def _record(conn, cursor, statement, parameters, context, executemany) -> None:
        normalized = statement.lstrip().upper()
        if normalized.startswith("SELECT") and "CATALOG.TOOL" in normalized:
            statements.append(statement)

    engine = get_engine().sync_engine
    event.listen(engine, "before_cursor_execute", _record)
    try:
        views = await service.list(actor=actor)
        list_count = len(statements)
        statements.clear()
        await service.get(actor=actor, tool_id=views[0].id)
        detail_count = len(statements)
    finally:
        event.remove(engine, "before_cursor_execute", _record)

    assert len(views) == 3
    assert list_count == 3
    assert detail_count == 3


async def test_tool_view_edge_cases(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str]
) -> None:
    from agent_platform.contexts.catalog.infrastructure.repositories import ToolRepository
    from agent_platform.shared.db import session_scope

    # 无版本工具：聚合字段为空值而不是缺字段
    async with session_scope(tenant_env.tenant_id) as session:
        bare = await ToolRepository().create(
            session,
            tenant_id=tenant_env.tenant_id,
            code=f"bare-{uuid.uuid4().hex[:6]}",
            name="Bare",
            description=None,
            type=ToolType.HTTP,
            status=ToolStatus.ACTIVE,
        )
        bare_id = bare.id

    detail = await api_client.get(f"/api/v1/tools/{bare_id}", headers=admin_headers)
    assert detail.status_code == 200
    assert detail.json()["risk_level"] is None
    assert detail.json()["latest_version_no"] is None
    assert detail.json()["granted"] is False

    # 未授权 -> 刚授权 -> 刚撤销
    tool = await _create_tool(api_client, admin_headers)
    ungranted = await api_client.get(f"/api/v1/tools/{tool['id']}", headers=admin_headers)
    assert ungranted.json()["granted"] is False

    granted = await api_client.post(f"/api/v1/tools/{tool['id']}/grant", headers=admin_headers)
    assert granted.status_code == 204
    after_grant = await api_client.get(f"/api/v1/tools/{tool['id']}", headers=admin_headers)
    assert after_grant.json()["granted"] is True

    revoked = await api_client.delete(f"/api/v1/tools/{tool['id']}/grant", headers=admin_headers)
    assert revoked.status_code == 204
    after_revoke = await api_client.get(f"/api/v1/tools/{tool['id']}", headers=admin_headers)
    assert after_revoke.json()["granted"] is False


async def test_tool_status_switch_is_idempotent(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str]
) -> None:
    tool = await _create_tool(api_client, admin_headers)

    first = await api_client.patch(
        f"/api/v1/tools/{tool['id']}/status",
        headers=admin_headers,
        json={"status": "DISABLED"},
    )
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "DISABLED"
    assert first.json()["latest_version_no"] == 1

    second = await api_client.patch(
        f"/api/v1/tools/{tool['id']}/status",
        headers=admin_headers,
        json={"status": "DISABLED"},
    )
    assert second.status_code == 200, second.text
    assert second.json()["status"] == "DISABLED"
    assert second.json()["updated_at"] == first.json()["updated_at"]


async def test_tool_grant_state_endpoint_covers_three_cases(
    api_client, tenant_env: TenantEnv, admin_headers: dict[str, str]
) -> None:
    tool = await _create_tool(api_client, admin_headers)

    ungranted = await api_client.get(f"/api/v1/tools/{tool['id']}/grant", headers=admin_headers)
    assert ungranted.status_code == 200, ungranted.text
    assert ungranted.json()["granted"] is False
    assert ungranted.json()["granted_at"] is None

    assert (
        await api_client.post(f"/api/v1/tools/{tool['id']}/grant", headers=admin_headers)
    ).status_code == 204
    granted = await api_client.get(f"/api/v1/tools/{tool['id']}/grant", headers=admin_headers)
    assert granted.status_code == 200
    assert granted.json()["granted"] is True
    assert granted.json()["granted_at"] is not None
    assert granted.json()["granted_by"] == str(tenant_env.user_id)

    missing = await api_client.get(f"/api/v1/tools/{uuid.uuid4()}/grant", headers=admin_headers)
    assert missing.status_code == 404

# ------------------------------------------------------------------ 模型可见契约


def _contract_spec() -> dict:
    return {
        "method": "POST",
        "url": "https://tools.local/weather",
        "timeout_s": 5,
        "input_schema": {
            "type": "object",
            "properties": {
                "city": {"type": "string"},
                "start_date": {"type": "string"},
            },
            "required": ["city", "start_date"],
        },
        "model_schema": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "中文城市名"},
                "start_date": {"type": "string", "format": "date"},
            },
            "required": ["city", "start_date"],
        },
        "returns": {"terminal_statuses": ["no_data"]},
    }


async def test_tool_version_reports_contract_warnings_without_blocking(
    api_client, admin_headers: dict[str, str]
) -> None:
    spec = _contract_spec()
    del spec["model_schema"]["properties"]["start_date"]["format"]
    tool = await _create_tool(api_client, admin_headers, spec=spec)

    version = await api_client.post(
        f"/api/v1/tools/{tool['id']}/versions",
        headers=admin_headers,
        json={"spec": spec},
    )
    assert version.status_code == 201, version.text
    body = version.json()
    assert body["spec"]["description"] == "echo service"
    assert body["spec"]["model_schema"] == spec["model_schema"]
    assert body["spec"]["returns"] == {"terminal_statuses": ["no_data"]}
    assert [item["field"] for item in body["warnings"]] == [
        "spec.input_schema.properties.start_date"
    ]

    listed = await api_client.get(
        f"/api/v1/tools/{tool['id']}/versions", headers=admin_headers
    )
    assert listed.status_code == 200
    assert listed.json()[0]["spec"]["model_schema"] == spec["model_schema"]


async def test_version_without_any_description_is_rejected(
    api_client, admin_headers: dict[str, str]
) -> None:
    created = await api_client.post(
        "/api/v1/tools",
        headers=admin_headers,
        json={
            "code": f"nodesc-{uuid.uuid4().hex[:6]}",
            "name": "NoDesc",
            "spec": _tool_spec(),
        },
    )
    assert created.status_code == 201, created.text

    rejected = await api_client.post(
        f"/api/v1/tools/{created.json()['id']}/versions",
        headers=admin_headers,
        json={"spec": _tool_spec()},
    )
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["error"]["details"][0]["field"] == "spec.description"


async def test_agent_snapshot_carries_model_contract(
    api_client, admin_headers: dict[str, str]
) -> None:
    spec = _contract_spec()
    tool = await _create_tool(api_client, admin_headers, spec=spec)
    assert (
        await api_client.post(f"/api/v1/tools/{tool['id']}/grant", headers=admin_headers)
    ).status_code == 204
    versions = await api_client.get(
        f"/api/v1/tools/{tool['id']}/versions", headers=admin_headers
    )
    tool_version_id = versions.json()[0]["id"]

    model = await _create_model(api_client, admin_headers, f"contract-{uuid.uuid4().hex[:6]}")
    prompt = await api_client.post(
        "/api/v1/prompts",
        headers=admin_headers,
        json={
            "code": f"contract-{uuid.uuid4().hex[:6]}",
            "name": "Contract",
            "messages": [{"role": "system", "content": "hi"}],
        },
    )
    assert prompt.status_code == 201, prompt.text
    prompt_version = (
        await api_client.get(
            f"/api/v1/prompts/{prompt.json()['id']}/versions", headers=admin_headers
        )
    ).json()[0]
    agent = await api_client.post(
        "/api/v1/agents",
        headers=admin_headers,
        json={"code": f"contract-{uuid.uuid4().hex[:6]}", "name": "Contract"},
    )
    assert agent.status_code == 201, agent.text

    version = await api_client.post(
        f"/api/v1/agents/{agent.json()['id']}/versions",
        headers=admin_headers,
        json={
            "model_id": model["id"],
            "prompt_version_id": prompt_version["id"],
            "tool_version_ids": [tool_version_id],
        },
    )
    assert version.status_code == 201, version.text
    snapshot_spec = version.json()["snapshot"]["tools"][0]["spec"]
    assert snapshot_spec["description"] == "echo service"
    assert snapshot_spec["model_schema"]["properties"]["start_date"]["format"] == "date"
    assert snapshot_spec["returns"] == {"terminal_statuses": ["no_data"]}
