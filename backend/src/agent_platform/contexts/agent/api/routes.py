"""agent 上下文的 HTTP 接口。"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, status

from agent_platform.contexts.agent.api.schemas import (
    AgentCopyRequest,
    AgentCreateRequest,
    AgentEnvironmentState,
    AgentListResponse,
    AgentPointerResponse,
    AgentReadinessResponse,
    AgentReleaseRequest,
    AgentReleaseResponse,
    AgentResponse,
    AgentRollbackRequest,
    AgentVersionCreateRequest,
    AgentVersionDiffResponse,
    AgentVersionResponse,
)
from agent_platform.contexts.agent.application.agent_service import agent_service
from agent_platform.contexts.agent.application.release_service import agent_release_service
from agent_platform.contexts.agent.domain.enums import AgentStatus, AgentType, Environment
from agent_platform.contexts.agent.infrastructure.tables import AgentRelease, AgentVersion
from agent_platform.contexts.platform.api.dependencies import CurrentActor
from agent_platform.contexts.platform.application.authorization_service import AuthorizationService
from agent_platform.contexts.platform.domain.permissions import Permission

router = APIRouter()
authorization = AuthorizationService()


def _agent_response(
    agent, environments: dict[str, dict[str, object] | None] | None = None
) -> AgentResponse:
    response = AgentResponse.model_validate(agent)
    return response.model_copy(
        update={
            "environments": {
                env: AgentEnvironmentState(**value) if value else None
                for env, value in (
                    environments
                    if environments is not None
                    else {env.value: None for env in Environment}
                ).items()
            }
        }
    )


def _release_response(release: AgentRelease, version: AgentVersion) -> AgentReleaseResponse:
    return AgentReleaseResponse(
        release_id=release.id,
        agent_id=release.agent_id,
        agent_version_id=release.agent_version_id,
        version_no=version.version_no,
        env=release.env,
        status=release.status.value,
        released_by=release.released_by,
        released_at=release.released_at,
        created_at=release.created_at,
    )


@router.post(
    "/agents",
    response_model=AgentResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["agents"],
    description=(
        "创建 Agent。可选 `config` 提供初始配置：在同一事务内装配并生成版本 1，"
        "任一引用不可用时整体回滚，不会留下无配置的 Agent。"
    ),
)
async def create_agent(payload: AgentCreateRequest, actor: CurrentActor) -> AgentResponse:
    await authorization.require_permission(actor, Permission.AGENT_MANAGE)
    config = payload.config
    agent = await agent_service.create(
        actor=actor,
        code=payload.code,
        name=payload.name,
        description=payload.description,
        tags=payload.tags,
        agent_type=payload.type,
        model_id=config.model_id if config else None,
        prompt_version_id=config.prompt_version_id if config else None,
        tool_version_ids=config.tool_version_ids if config else None,
        params=config.params if config else None,
        policy=config.policy if config else None,
    )
    return _agent_response(agent)


@router.post(
    "/agents/{agent_id}/copy",
    response_model=AgentResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["agents"],
    description=(
        "以新编码复制 Agent。来源存在版本时复制其最新快照为版本 1，`config_hash` 与来源一致；"
        "不复制发布记录、发布指针与运行历史。来源无版本时只创建身份。"
    ),
)
async def copy_agent(
    agent_id: uuid.UUID, payload: AgentCopyRequest, actor: CurrentActor
) -> AgentResponse:
    await authorization.require_permission(actor, Permission.AGENT_MANAGE)
    agent = await agent_service.copy(
        actor=actor,
        agent_id=agent_id,
        code=payload.code,
        name=payload.name,
    )
    return _agent_response(agent)


@router.get(
    "/agents",
    response_model=AgentListResponse,
    tags=["agents"],
    description=(
        "服务端分页列出 Agent，按更新时间倒序。支持按状态、类型、标签与关键字（编码/名称）筛选，"
        "每项附带 DEV/TEST/PROD 三环境当前生效版本，未发布环境为 null。"
    ),
)
async def list_agents(
    actor: CurrentActor,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status_filter: AgentStatus | None = Query(default=None, alias="status"),
    type_filter: AgentType | None = Query(default=None, alias="type"),
    tag: str | None = Query(default=None, max_length=64),
    q: str | None = Query(default=None, max_length=160),
) -> AgentListResponse:
    await authorization.require_permission(actor, Permission.AGENT_READ)
    items, total = await agent_service.list_page(
        actor=actor,
        status=status_filter,
        agent_type=type_filter,
        tag=tag,
        q=q,
        page=page,
        page_size=page_size,
    )
    return AgentListResponse(
        items=[
            _agent_response(item.agent, item.environments) for item in items
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/agents/{agent_id}",
    response_model=AgentResponse,
    tags=["agents"],
    description="Agent 详情，附 DEV/TEST/PROD 三环境当前生效版本与发布时间。",
)
async def get_agent(agent_id: uuid.UUID, actor: CurrentActor) -> AgentResponse:
    await authorization.require_permission(actor, Permission.AGENT_READ)
    item = await agent_service.get_detail(actor=actor, agent_id=agent_id)
    return _agent_response(item.agent, item.environments)


@router.post(
    "/agents/{agent_id}/activate",
    response_model=AgentResponse,
    tags=["agents"],
    description=(
        "重新启用 Agent，仅对已成功发布过的 Agent 有效；不改动发布记录与指针。"
        "从未发布的 Agent 返回 409 `AGENT_NOT_PUBLISHED`。"
    ),
)
async def activate_agent(agent_id: uuid.UUID, actor: CurrentActor) -> AgentResponse:
    await authorization.require_permission(actor, Permission.AGENT_MANAGE)
    agent = await agent_service.activate(actor=actor, agent_id=agent_id)
    return _agent_response(agent)


@router.post(
    "/agents/{agent_id}/deactivate",
    response_model=AgentResponse,
    tags=["agents"],
    description=(
        "停用 Agent：立即阻断该 Agent 在所有环境的新调用，发布记录与指针保持不变。"
        "调用方收到 409 `AGENT_INACTIVE`。"
    ),
)
async def deactivate_agent(agent_id: uuid.UUID, actor: CurrentActor) -> AgentResponse:
    await authorization.require_permission(actor, Permission.AGENT_MANAGE)
    agent = await agent_service.deactivate(actor=actor, agent_id=agent_id)
    return _agent_response(agent)


@router.post(
    "/agents/{agent_id}/archive",
    response_model=AgentResponse,
    tags=["agents"],
    description=(
        "归档 Agent：终态，保留编码与全部历史，不可再编辑、发布、回滚或启用。"
        "非法规避由 409 `AGENT_STATUS_TRANSITION` 拒绝。"
    ),
)
async def archive_agent(agent_id: uuid.UUID, actor: CurrentActor) -> AgentResponse:
    await authorization.require_permission(actor, Permission.AGENT_MANAGE)
    agent = await agent_service.archive(actor=actor, agent_id=agent_id)
    return _agent_response(agent)


@router.delete(
    "/agents/{agent_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["agents"],
    description=(
        "删除从未产生版本、发布与运行记录的 Agent，编码随之释放；"
        "存在任一历史时返回 409 `AGENT_HAS_HISTORY` 并提示改为归档。"
    ),
)
async def delete_agent(agent_id: uuid.UUID, actor: CurrentActor) -> None:
    await authorization.require_permission(actor, Permission.AGENT_MANAGE)
    await agent_service.delete(actor=actor, agent_id=agent_id)


@router.post(
    "/agents/{agent_id}/versions",
    response_model=AgentVersionResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["agents"],
    description=(
        "按引用装配并生成不可变版本。可选 `base_version_id` 声明编辑基准；"
        "基准不是最新版本时返回 409 `AGENT_VERSION_CONFLICT`，`details[0].diff` 给出差异摘要，"
        "确认后省略该字段重新提交即可继续保存。"
    ),
)
async def create_agent_version(
    agent_id: uuid.UUID, payload: AgentVersionCreateRequest, actor: CurrentActor
) -> AgentVersionResponse:
    await authorization.require_permission(actor, Permission.AGENT_MANAGE)
    version = await agent_service.create_version(
        actor=actor,
        agent_id=agent_id,
        model_id=payload.model_id,
        prompt_version_id=payload.prompt_version_id,
        tool_version_ids=payload.tool_version_ids,
        params=payload.params,
        policy=payload.policy,
        note=payload.note,
        base_version_id=payload.base_version_id,
    )
    return AgentVersionResponse.model_validate(version)


@router.get(
    "/agents/{agent_id}/versions",
    response_model=list[AgentVersionResponse],
    tags=["agents"],
)
async def list_agent_versions(agent_id: uuid.UUID, actor: CurrentActor) -> list[AgentVersionResponse]:
    await authorization.require_permission(actor, Permission.AGENT_READ)
    versions = await agent_service.list_versions(actor=actor, agent_id=agent_id)
    return [AgentVersionResponse.model_validate(version) for version in versions]


@router.get(
    "/agents/{agent_id}/versions/{version_id}/readiness",
    response_model=AgentReadinessResponse,
    tags=["agents"],
    description=(
        "发布前体检：逐个绑定返回可用性与不可用原因（模型、Prompt 版本、每个工具），"
        "并附最近一次同 `config_hash` 的预览摘要（无预览时为 null）。"
    ),
)
async def get_agent_version_readiness(
    agent_id: uuid.UUID, version_id: uuid.UUID, actor: CurrentActor
) -> AgentReadinessResponse:
    await authorization.require_permission(actor, Permission.AGENT_READ)
    readiness = await agent_service.readiness(
        actor=actor, agent_id=agent_id, version_id=version_id
    )
    return AgentReadinessResponse(
        agent_id=readiness.agent_id,
        agent_version_id=readiness.agent_version_id,
        version_no=readiness.version_no,
        config_hash=readiness.config_hash,
        ready=readiness.ready,
        bindings=readiness.bindings,
        preview=readiness.preview,
    )


@router.get(
    "/agent-versions/diff",
    response_model=AgentVersionDiffResponse,
    tags=["agents"],
)
async def diff_agent_versions(
    actor: CurrentActor,
    base_version_id: uuid.UUID = Query(),
    target_version_id: uuid.UUID = Query(),
) -> AgentVersionDiffResponse:
    await authorization.require_permission(actor, Permission.AGENT_READ)
    diff = await agent_service.diff_versions(
        actor=actor,
        base_version_id=base_version_id,
        target_version_id=target_version_id,
    )
    return AgentVersionDiffResponse.model_validate(diff)


@router.get(
    "/agent-versions/{version_id}",
    response_model=AgentVersionResponse,
    tags=["agents"],
)
async def get_agent_version(version_id: uuid.UUID, actor: CurrentActor) -> AgentVersionResponse:
    await authorization.require_permission(actor, Permission.AGENT_READ)
    version = await agent_service.get_version(actor=actor, version_id=version_id)
    return AgentVersionResponse.model_validate(version)


@router.post(
    "/agents/{agent_id}/releases",
    response_model=AgentReleaseResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["agents"],
)
async def publish_agent(
    agent_id: uuid.UUID, payload: AgentReleaseRequest, actor: CurrentActor
) -> AgentReleaseResponse:
    await authorization.require_permission(actor, Permission.AGENT_PUBLISH)
    release, version = await agent_release_service.publish(
        actor=actor,
        agent_id=agent_id,
        agent_version_id=payload.agent_version_id,
        env=payload.env,
        note=payload.note,
    )
    return _release_response(release, version)


@router.get(
    "/agents/{agent_id}/releases",
    response_model=list[AgentReleaseResponse],
    tags=["agents"],
)
async def list_agent_releases(
    agent_id: uuid.UUID,
    actor: CurrentActor,
    env: Environment | None = Query(default=None),
) -> list[AgentReleaseResponse]:
    await authorization.require_permission(actor, Permission.AGENT_READ)
    rows = await agent_release_service.list_releases(actor=actor, agent_id=agent_id, env=env)
    return [_release_response(release, version) for release, version in rows]


@router.get(
    "/agents/{agent_id}/pointers/{env}",
    response_model=AgentPointerResponse,
    tags=["agents"],
)
async def get_agent_pointer(
    agent_id: uuid.UUID, env: Environment, actor: CurrentActor
) -> AgentPointerResponse:
    await authorization.require_permission(actor, Permission.AGENT_READ)
    pointer = await agent_release_service.get_pointer(actor=actor, agent_id=agent_id, env=env)
    return AgentPointerResponse(
        agent_id=pointer.agent_id,
        env=pointer.env,
        release_id=pointer.release_id,
        agent_version_id=pointer.agent_version_id,
        version_no=pointer.version_no,
        updated_at=pointer.updated_at,
    )


@router.post(
    "/agents/{agent_id}/rollback",
    response_model=AgentReleaseResponse,
    tags=["agents"],
)
async def rollback_agent(
    agent_id: uuid.UUID, payload: AgentRollbackRequest, actor: CurrentActor
) -> AgentReleaseResponse:
    await authorization.require_permission(actor, Permission.AGENT_PUBLISH)
    release, version = await agent_release_service.rollback(
        actor=actor,
        agent_id=agent_id,
        env=payload.env,
        release_id=payload.release_id,
    )
    return _release_response(release, version)
