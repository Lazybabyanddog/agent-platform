"""catalog 上下文的 HTTP 接口。"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, status

from agent_platform.contexts.catalog.api.schemas import (
    ModelCreateRequest,
    ModelResponse,
    ModelStatusRequest,
    ModelUpdateRequest,
    ParamsValidateRequest,
    ParamsValidateResponse,
    ProbeRecordResponse,
    ProbeResponse,
    ProbeSummary,
    PromptCreateRequest,
    PromptDiffResponse,
    PromptRenderRequest,
    PromptRenderResponse,
    PromptResponse,
    PromptVersionCreateRequest,
    PromptVersionResponse,
    ProviderCreateRequest,
    ProviderResponse,
    ProviderUpdateRequest,
    ToolCreateRequest,
    ToolExecuteRequest,
    ToolExecuteResponse,
    ToolGrantResponse,
    ToolResponse,
    ToolStatusRequest,
    ToolVersionCreateRequest,
    ToolVersionResponse,
)
from agent_platform.contexts.catalog.application.model_service import ProbeResult, model_service
from agent_platform.contexts.catalog.application.prompt_service import prompt_service
from agent_platform.contexts.catalog.application.tool_service import tool_service
from agent_platform.contexts.catalog.domain.params import normalize_params
from agent_platform.contexts.catalog.infrastructure.tables import ModelProbe, ModelProvider
from agent_platform.contexts.platform.api.dependencies import CurrentActor
from agent_platform.contexts.platform.application.authorization_service import AuthorizationService
from agent_platform.contexts.platform.domain.permissions import Permission
from agent_platform.shared.errors import ValidationFailedError

router = APIRouter()
authorization = AuthorizationService()

PROVIDER_IMPACT_NOTICE = (
    "Base URL 与凭据引用在调用时活取：该变更对引用此供应商的全部已发布 Agent 版本立即生效"
)

_MODEL_IMMUTABLE_FIELDS = ("code", "provider_id")


def _provider_response(
    provider: ModelProvider,
    latest_probe: ModelProbe | None = None,
    *,
    impact_notice: str | None = None,
) -> ProviderResponse:
    response = ProviderResponse.model_validate(provider)
    if latest_probe is not None:
        response.latest_probe = ProbeSummary(
            verdict=str(latest_probe.verdict),
            created_at=latest_probe.created_at,
            observed_model_name=latest_probe.observed_model_name,
        )
    response.impact_notice = impact_notice
    return response


def _reject_immutable_model_fields(payload: ModelUpdateRequest) -> None:
    for field in _MODEL_IMMUTABLE_FIELDS:
        if field in payload.model_fields_set:
            reason = (
                "is immutable: agent versions reference the code; create a new model instead"
                if field == "code"
                else (
                    "is immutable: changing the provider would swap the upstream of "
                    "released versions; create a new model instead"
                )
            )
            raise ValidationFailedError.for_field(field, reason)


# ------------------------------------------------------------------ 供应商


@router.post(
    "/model-providers",
    response_model=ProviderResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["models"],
)
async def create_provider(payload: ProviderCreateRequest, actor: CurrentActor) -> ProviderResponse:
    await authorization.require_permission(actor, Permission.MODEL_MANAGE)
    provider = await model_service.create_provider(
        actor=actor,
        name=payload.name,
        base_url=payload.base_url,
        adapter=payload.adapter,
        secret_ref_id=payload.secret_id,
        region=payload.region,
    )
    return _provider_response(provider)


@router.get("/model-providers", response_model=list[ProviderResponse], tags=["models"])
async def list_providers(actor: CurrentActor) -> list[ProviderResponse]:
    await authorization.require_permission(actor, Permission.MODEL_READ)
    providers = await model_service.list_providers(actor=actor)
    probes = await model_service.latest_provider_probes(actor=actor)
    return [_provider_response(provider, probes.get(provider.id)) for provider in providers]


@router.patch(
    "/model-providers/{provider_id}", response_model=ProviderResponse, tags=["models"]
)
async def update_provider(
    provider_id: uuid.UUID, payload: ProviderUpdateRequest, actor: CurrentActor
) -> ProviderResponse:
    await authorization.require_permission(actor, Permission.MODEL_MANAGE)
    changes = payload.model_dump(exclude_unset=True, exclude_none=True)
    secret_id = changes.pop("secret_id", None)
    if secret_id is not None:
        changes["secret_ref_id"] = secret_id
    provider = await model_service.update_provider(
        actor=actor, provider_id=provider_id, changes=changes
    )
    probes = await model_service.latest_provider_probes(actor=actor)
    impact = (
        PROVIDER_IMPACT_NOTICE
        if {"base_url", "secret_ref_id"} & set(changes)
        else None
    )
    return _provider_response(provider, probes.get(provider.id), impact_notice=impact)


# ------------------------------------------------------------------ 模型


@router.post(
    "/models", response_model=ModelResponse, status_code=status.HTTP_201_CREATED, tags=["models"]
)
async def create_model(payload: ModelCreateRequest, actor: CurrentActor) -> ModelResponse:
    await authorization.require_permission(actor, Permission.MODEL_MANAGE)
    model = await model_service.create_model(actor=actor, **payload.model_dump())
    return ModelResponse.model_validate(model)


@router.get("/models", response_model=list[ModelResponse], tags=["models"])
async def list_models(actor: CurrentActor) -> list[ModelResponse]:
    await authorization.require_permission(actor, Permission.MODEL_READ)
    models = await model_service.list_models(actor=actor)
    return [ModelResponse.model_validate(m) for m in models]


@router.get("/models/{model_id}", response_model=ModelResponse, tags=["models"])
async def get_model(model_id: uuid.UUID, actor: CurrentActor) -> ModelResponse:
    await authorization.require_permission(actor, Permission.MODEL_READ)
    return ModelResponse.model_validate(await model_service.get_model(actor=actor, model_id=model_id))


@router.patch("/models/{model_id}", response_model=ModelResponse, tags=["models"])
async def update_model(
    model_id: uuid.UUID, payload: ModelUpdateRequest, actor: CurrentActor
) -> ModelResponse:
    await authorization.require_permission(actor, Permission.MODEL_MANAGE)
    _reject_immutable_model_fields(payload)
    changes = payload.model_dump(
        exclude_unset=True, exclude_none=True, exclude={"code", "provider_id"}
    )
    model = await model_service.update_model(actor=actor, model_id=model_id, changes=changes)
    return ModelResponse.model_validate(model)


@router.patch("/models/{model_id}/status", response_model=ModelResponse, tags=["models"])
async def set_model_status(
    model_id: uuid.UUID, payload: ModelStatusRequest, actor: CurrentActor
) -> ModelResponse:
    await authorization.require_permission(actor, Permission.MODEL_MANAGE)
    model = await model_service.set_status(actor=actor, model_id=model_id, status=payload.status)
    return ModelResponse.model_validate(model)


@router.post("/models/{model_id}/validate-params", response_model=ParamsValidateResponse, tags=["models"])
async def validate_model_params(
    model_id: uuid.UUID, payload: ParamsValidateRequest, actor: CurrentActor
) -> ParamsValidateResponse:
    await authorization.require_permission(actor, Permission.MODEL_READ)
    model = await model_service.get_model(actor=actor, model_id=model_id)
    return ParamsValidateResponse(params=normalize_params(model.param_template, payload.params))


def _probe_response(result: ProbeResult) -> ProbeResponse:
    assert result.probe_id is not None and result.created_at is not None
    return ProbeResponse(
        ok=result.ok,
        verdict=result.verdict,
        status_code=result.status_code,
        latency_ms=result.latency_ms,
        model_count=result.model_count,
        error_code=result.error_code,
        observed_models=result.observed_models,
        observed_model_name=result.observed_model_name,
        probe_id=result.probe_id,
        created_at=result.created_at,
    )


@router.post("/models/{model_id}/probe", response_model=ProbeResponse, tags=["models"])
async def probe_model(model_id: uuid.UUID, actor: CurrentActor) -> ProbeResponse:
    await authorization.require_permission(actor, Permission.MODEL_MANAGE)
    return _probe_response(await model_service.probe(actor=actor, model_id=model_id))


@router.get(
    "/models/{model_id}/probes", response_model=list[ProbeRecordResponse], tags=["models"]
)
async def list_model_probes(
    model_id: uuid.UUID, actor: CurrentActor, limit: int = Query(default=20, ge=1, le=100)
) -> list[ProbeRecordResponse]:
    await authorization.require_permission(actor, Permission.MODEL_READ)
    records = await model_service.model_probes(actor=actor, model_id=model_id, limit=limit)
    return [ProbeRecordResponse.model_validate(record) for record in records]


@router.post(
    "/model-providers/{provider_id}/probe", response_model=ProbeResponse, tags=["models"]
)
async def probe_provider(provider_id: uuid.UUID, actor: CurrentActor) -> ProbeResponse:
    await authorization.require_permission(actor, Permission.MODEL_MANAGE)
    return _probe_response(
        await model_service.probe_provider(actor=actor, provider_id=provider_id)
    )


@router.get(
    "/model-providers/{provider_id}/probes",
    response_model=list[ProbeRecordResponse],
    tags=["models"],
)
async def list_provider_probes(
    provider_id: uuid.UUID,
    actor: CurrentActor,
    limit: int = Query(default=20, ge=1, le=100),
) -> list[ProbeRecordResponse]:
    await authorization.require_permission(actor, Permission.MODEL_READ)
    records = await model_service.provider_probes(
        actor=actor, provider_id=provider_id, limit=limit
    )
    return [ProbeRecordResponse.model_validate(record) for record in records]


# ------------------------------------------------------------------ Prompt


@router.post(
    "/prompts", response_model=PromptResponse, status_code=status.HTTP_201_CREATED, tags=["prompts"]
)
async def create_prompt(payload: PromptCreateRequest, actor: CurrentActor) -> PromptResponse:
    await authorization.require_permission(actor, Permission.PROMPT_MANAGE)
    prompt, _version = await prompt_service.create(
        actor=actor,
        code=payload.code,
        name=payload.name,
        description=payload.description,
        messages=payload.messages,
        variables=payload.variables,
        note=payload.note,
    )
    return PromptResponse.model_validate(prompt)


@router.get("/prompts", response_model=list[PromptResponse], tags=["prompts"])
async def list_prompts(actor: CurrentActor) -> list[PromptResponse]:
    await authorization.require_permission(actor, Permission.PROMPT_READ)
    prompts = await prompt_service.list(actor=actor)
    return [PromptResponse.model_validate(p) for p in prompts]


@router.get("/prompts/{prompt_id}", response_model=PromptResponse, tags=["prompts"])
async def get_prompt(prompt_id: uuid.UUID, actor: CurrentActor) -> PromptResponse:
    await authorization.require_permission(actor, Permission.PROMPT_READ)
    return PromptResponse.model_validate(await prompt_service.get(actor=actor, prompt_id=prompt_id))


@router.post(
    "/prompts/{prompt_id}/versions",
    response_model=PromptVersionResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["prompts"],
)
async def add_prompt_version(
    prompt_id: uuid.UUID, payload: PromptVersionCreateRequest, actor: CurrentActor
) -> PromptVersionResponse:
    await authorization.require_permission(actor, Permission.PROMPT_MANAGE)
    version = await prompt_service.add_version(
        actor=actor,
        prompt_id=prompt_id,
        messages=payload.messages,
        variables=payload.variables,
        note=payload.note,
    )
    return PromptVersionResponse.model_validate(version)


@router.get(
    "/prompts/{prompt_id}/versions",
    response_model=list[PromptVersionResponse],
    tags=["prompts"],
)
async def list_prompt_versions(prompt_id: uuid.UUID, actor: CurrentActor) -> list[PromptVersionResponse]:
    await authorization.require_permission(actor, Permission.PROMPT_READ)
    versions = await prompt_service.list_versions(actor=actor, prompt_id=prompt_id)
    return [PromptVersionResponse.model_validate(v) for v in versions]


@router.get("/prompt-versions/diff", response_model=PromptDiffResponse, tags=["prompts"])
async def diff_prompt_versions(
    base: uuid.UUID, target: uuid.UUID, actor: CurrentActor
) -> PromptDiffResponse:
    await authorization.require_permission(actor, Permission.PROMPT_READ)
    diff = await prompt_service.diff(actor=actor, base_version_id=base, target_version_id=target)
    return PromptDiffResponse(
        base_version_no=diff.base_version_no,
        target_version_no=diff.target_version_no,
        identical=diff.identical,
        message_changes=diff.message_changes,
        variables_added=diff.variables_added,
        variables_removed=diff.variables_removed,
        variables_changed=diff.variables_changed,
    )


@router.get(
    "/prompt-versions/{version_id}", response_model=PromptVersionResponse, tags=["prompts"]
)
async def get_prompt_version(version_id: uuid.UUID, actor: CurrentActor) -> PromptVersionResponse:
    await authorization.require_permission(actor, Permission.PROMPT_READ)
    version = await prompt_service.get_version(actor=actor, version_id=version_id)
    return PromptVersionResponse.model_validate(version)


@router.post(
    "/prompt-versions/{version_id}/render", response_model=PromptRenderResponse, tags=["prompts"]
)
async def render_prompt_version(
    version_id: uuid.UUID, payload: PromptRenderRequest, actor: CurrentActor
) -> PromptRenderResponse:
    await authorization.require_permission(actor, Permission.PROMPT_READ)
    messages = await prompt_service.preview(
        actor=actor, version_id=version_id, values=payload.values
    )
    return PromptRenderResponse(messages=messages)


# ------------------------------------------------------------------ 工具


@router.post(
    "/tools", response_model=ToolResponse, status_code=status.HTTP_201_CREATED, tags=["tools"]
)
async def create_tool(payload: ToolCreateRequest, actor: CurrentActor) -> ToolResponse:
    await authorization.require_permission(actor, Permission.TOOL_MANAGE)
    tool, _version = await tool_service.create(
        actor=actor,
        code=payload.code,
        name=payload.name,
        description=payload.description,
        type=payload.type,
        spec=payload.spec,
        risk_level=payload.risk_level,
    )
    return ToolResponse.model_validate(tool)


@router.get("/tools", response_model=list[ToolResponse], tags=["tools"])
async def list_tools(actor: CurrentActor) -> list[ToolResponse]:
    await authorization.require_permission(actor, Permission.TOOL_READ)
    tools = await tool_service.list(actor=actor)
    return [ToolResponse.model_validate(t) for t in tools]


@router.post(
    "/tools/{tool_id}/versions",
    response_model=ToolVersionResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["tools"],
)
async def add_tool_version(
    tool_id: uuid.UUID, payload: ToolVersionCreateRequest, actor: CurrentActor
) -> ToolVersionResponse:
    await authorization.require_permission(actor, Permission.TOOL_MANAGE)
    version = await tool_service.add_version(
        actor=actor, tool_id=tool_id, spec=payload.spec, risk_level=payload.risk_level
    )
    return ToolVersionResponse.model_validate(version)


@router.get("/tools/{tool_id}/versions", response_model=list[ToolVersionResponse], tags=["tools"])
async def list_tool_versions(tool_id: uuid.UUID, actor: CurrentActor) -> list[ToolVersionResponse]:
    await authorization.require_permission(actor, Permission.TOOL_READ)
    versions = await tool_service.list_versions(actor=actor, tool_id=tool_id)
    return [ToolVersionResponse.model_validate(v) for v in versions]


@router.patch("/tools/{tool_id}/status", response_model=ToolResponse, tags=["tools"])
async def set_tool_status(
    tool_id: uuid.UUID, payload: ToolStatusRequest, actor: CurrentActor
) -> ToolResponse:
    await authorization.require_permission(actor, Permission.TOOL_MANAGE)
    tool = await tool_service.set_status(actor=actor, tool_id=tool_id, status=payload.status)
    return ToolResponse.model_validate(tool)


@router.get("/tools/{tool_id}/grant", response_model=ToolGrantResponse, tags=["tools"])
async def get_tool_grant(tool_id: uuid.UUID, actor: CurrentActor) -> ToolGrantResponse:
    await authorization.require_permission(actor, Permission.TOOL_READ)
    grant = await tool_service.get_grant(actor=actor, tool_id=tool_id)
    return ToolGrantResponse.model_validate(grant)


@router.post("/tools/{tool_id}/grant", status_code=status.HTTP_204_NO_CONTENT, tags=["tools"])
async def grant_tool(tool_id: uuid.UUID, actor: CurrentActor) -> None:
    await authorization.require_permission(actor, Permission.TOOL_MANAGE)
    await tool_service.grant(actor=actor, tool_id=tool_id)


@router.delete("/tools/{tool_id}/grant", status_code=status.HTTP_204_NO_CONTENT, tags=["tools"])
async def revoke_tool(tool_id: uuid.UUID, actor: CurrentActor) -> None:
    await authorization.require_permission(actor, Permission.TOOL_MANAGE)
    await tool_service.revoke(actor=actor, tool_id=tool_id)


@router.post(
    "/tool-versions/{version_id}/execute",
    response_model=ToolExecuteResponse,
    tags=["tools"],
)
async def execute_tool_version(
    version_id: uuid.UUID, payload: ToolExecuteRequest, actor: CurrentActor
) -> ToolExecuteResponse:
    await authorization.require_permission(actor, Permission.TOOL_MANAGE)
    result = await tool_service.execute(
        actor=actor,
        tool_version_id=version_id,
        arguments=payload.arguments,
        allow_high_risk=payload.allow_high_risk,
    )
    return ToolExecuteResponse(
        status=result.status,
        output=result.output,
        latency_ms=result.latency_ms,
        request_digest=result.request_digest,
        response_digest=result.response_digest,
        error_code=result.error_code,
    )


@router.get("/tools/{tool_id}", response_model=ToolResponse, tags=["tools"])
async def get_tool(tool_id: uuid.UUID, actor: CurrentActor) -> ToolResponse:
    await authorization.require_permission(actor, Permission.TOOL_READ)
    return ToolResponse.model_validate(await tool_service.get(actor=actor, tool_id=tool_id))
