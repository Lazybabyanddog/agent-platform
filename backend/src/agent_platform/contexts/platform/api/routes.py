"""platform 上下文的 HTTP 接口。

路由只做三件事：取已认证的 ActorContext、调用 application 用例、把结果映射成响应模型。
权限判断统一走 AuthorizationService，不在路由里自己比较角色。
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from agent_platform.contexts.platform.api.dependencies import CurrentActor, CurrentPage
from agent_platform.contexts.platform.api.schemas import (
    ApiKeyCreateRequest,
    ApiKeyCreateResponse,
    ApiKeyResponse,
    AuditLogResponse,
    GrantCreateRequest,
    GrantResponse,
    InsightRetentionRequest,
    LoginRequest,
    LoginResponse,
    MemberCreateRequest,
    MemberResponse,
    MeResponse,
    Page,
    RegisterRequest,
    SecretCreateRequest,
    SecretResponse,
    SecretRotateRequest,
    TenantCreateRequest,
    TenantResponse,
    TenantStatusRequest,
    UserCreatedResponse,
)
from agent_platform.contexts.platform.application.api_key_service import api_key_service
from agent_platform.contexts.platform.application.audit_service import audit_service
from agent_platform.contexts.platform.application.auth_service import auth_service
from agent_platform.contexts.platform.application.authorization_service import AuthorizationService
from agent_platform.contexts.platform.application.grant_service import grant_service
from agent_platform.contexts.platform.application.secret_service import secret_service
from agent_platform.contexts.platform.application.tenant_service import TenantService
from agent_platform.contexts.platform.domain.enums import AuditResult
from agent_platform.contexts.platform.domain.permissions import Permission
from agent_platform.shared.db import session_scope
from agent_platform.shared.errors import AppError

router = APIRouter()
tenant_service = TenantService()
authorization = AuthorizationService()


# ------------------------------------------------------------------ 认证


@router.post(
    "/auth/register",
    response_model=UserCreatedResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["auth"],
)
async def register(payload: RegisterRequest) -> UserCreatedResponse:
    user_id = await auth_service.register(
        tenant_code=payload.tenant_code,
        email=payload.email,
        password=payload.password,
        display_name=payload.display_name,
    )
    return UserCreatedResponse(user_id=user_id)


@router.post("/auth/login", response_model=LoginResponse, tags=["auth"])
async def login(payload: LoginRequest) -> LoginResponse:
    try:
        result = await auth_service.login(
            tenant_code=payload.tenant_code, email=payload.email, password=payload.password
        )
    except AppError as exc:
        # 失败登录也留痕；租户不存在时无从落租户审计，直接按原错误返回
        tenant = await tenant_service.find_by_code(payload.tenant_code)
        if tenant is not None:
            await audit_service.record_detached(
                tenant_id=tenant.id,
                action="auth.login_failed",
                resource_type="user_account",
                result=AuditResult.FAILURE,
                detail={"email": payload.email, "code": exc.code},
            )
        raise
    return LoginResponse(
        access_token=result.token,
        expires_in=result.expires_in,
        display_name=result.display_name,
        tenant_id=result.actor.tenant_id,
        actor_id=result.actor.actor_id,
        permissions=sorted(result.actor.permissions),
    )


@router.get("/auth/me", response_model=MeResponse, tags=["auth"])
async def me(actor: CurrentActor) -> MeResponse:
    return MeResponse(
        tenant_id=actor.tenant_id,
        actor_id=actor.actor_id,
        actor_type=actor.actor_type,
        display_name=actor.display_name,
        permissions=sorted(actor.permissions),
        roles=sorted(actor.roles),
    )


# ------------------------------------------------------------------ 租户


@router.post(
    "/tenants",
    response_model=TenantResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["tenants"],
)
async def create_tenant(payload: TenantCreateRequest, actor: CurrentActor) -> TenantResponse:
    await authorization.require_permission(actor, Permission.TENANT_MANAGE)
    tenant = await tenant_service.create(
        code=payload.code, name=payload.name, description=payload.description, actor=actor
    )
    return TenantResponse.model_validate(tenant)


@router.get("/tenants", response_model=Page[TenantResponse], tags=["tenants"])
async def list_tenants(actor: CurrentActor, page: CurrentPage) -> Page[TenantResponse]:
    await authorization.require_permission(actor, Permission.TENANT_READ)
    tenants = await tenant_service.list(limit=page.limit, offset=page.offset)
    return Page[TenantResponse](
        items=[TenantResponse.model_validate(t) for t in tenants], total=len(tenants)
    )


@router.get("/tenants/{tenant_id}", response_model=TenantResponse, tags=["tenants"])
async def get_tenant(tenant_id: uuid.UUID, actor: CurrentActor) -> TenantResponse:
    await authorization.require_permission(actor, Permission.TENANT_READ)
    tenant = await tenant_service.get(tenant_id)
    return TenantResponse.model_validate(tenant)


@router.patch(
    "/tenants/{tenant_id}/insight-retention",
    response_model=TenantResponse,
    tags=["tenants"],
)
async def set_insight_retention(
    tenant_id: uuid.UUID, payload: InsightRetentionRequest, actor: CurrentActor
) -> TenantResponse:
    await authorization.require_permission(actor, Permission.TENANT_MANAGE)
    tenant = await tenant_service.set_content_retention(
        tenant_id=tenant_id, enabled=payload.enabled, actor=actor
    )
    return TenantResponse.model_validate(tenant)


@router.patch("/tenants/{tenant_id}/status", tags=["tenants"])
async def set_tenant_status(
    tenant_id: uuid.UUID, payload: TenantStatusRequest, actor: CurrentActor
) -> dict[str, str]:
    await authorization.require_permission(actor, Permission.TENANT_MANAGE)
    await tenant_service.set_status(tenant_id=tenant_id, status=payload.status, actor=actor)
    return {"tenant_id": str(tenant_id), "status": payload.status.value}


# ------------------------------------------------------------------ 成员


@router.get("/members", response_model=Page[MemberResponse], tags=["members"])
async def list_members(actor: CurrentActor) -> Page[MemberResponse]:
    await authorization.require_permission(actor, Permission.TENANT_READ)
    members = await tenant_service.list_members(actor.tenant_id)
    return Page[MemberResponse](
        items=[MemberResponse.model_validate(m) for m in members], total=len(members)
    )


@router.post(
    "/members",
    response_model=MemberResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["members"],
)
async def add_member(payload: MemberCreateRequest, actor: CurrentActor) -> MemberResponse:
    await authorization.require_permission(actor, Permission.TENANT_MANAGE)
    membership = await tenant_service.add_member(
        tenant_id=actor.tenant_id,
        user_id=payload.user_id,
        actor=actor,
        department=payload.department,
        role_code=payload.role_code,
    )
    return MemberResponse.model_validate(membership)


@router.delete("/members/{membership_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["members"])
async def disable_member(membership_id: uuid.UUID, actor: CurrentActor) -> None:
    await authorization.require_permission(actor, Permission.TENANT_MANAGE)
    await tenant_service.disable_member(
        tenant_id=actor.tenant_id, membership_id=membership_id, actor=actor
    )


# ------------------------------------------------------------------ API Key


@router.post(
    "/api-keys",
    response_model=ApiKeyCreateResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["api-keys"],
)
async def create_api_key(payload: ApiKeyCreateRequest, actor: CurrentActor) -> ApiKeyCreateResponse:
    await authorization.require_permission(actor, Permission.APIKEY_MANAGE)
    key, plaintext = await api_key_service.create(
        actor=actor,
        name=payload.name,
        scopes=payload.scopes,
        expires_in_days=payload.expires_in_days,
    )
    return ApiKeyCreateResponse(
        id=key.id,
        key_id=key.key_id,
        name=key.name,
        secret=plaintext,
        scopes=list(key.scopes or []),
        expires_at=key.expires_at,
    )


@router.get("/api-keys", response_model=Page[ApiKeyResponse], tags=["api-keys"])
async def list_api_keys(actor: CurrentActor) -> Page[ApiKeyResponse]:
    await authorization.require_permission(actor, Permission.APIKEY_MANAGE)
    keys = await api_key_service.list(actor.tenant_id)
    return Page[ApiKeyResponse](
        items=[ApiKeyResponse.model_validate(k) for k in keys], total=len(keys)
    )


@router.delete("/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["api-keys"])
async def disable_api_key(key_id: uuid.UUID, actor: CurrentActor) -> None:
    await authorization.require_permission(actor, Permission.APIKEY_MANAGE)
    await api_key_service.disable(actor=actor, key_id=key_id)


# ------------------------------------------------------------------ 凭据


@router.post(
    "/secrets",
    response_model=SecretResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["secrets"],
)
async def create_secret(payload: SecretCreateRequest, actor: CurrentActor) -> SecretResponse:
    await authorization.require_permission(actor, Permission.SECRET_MANAGE)
    view = await secret_service.create(actor=actor, name=payload.name, value=payload.value)
    return SecretResponse.model_validate(view, from_attributes=True)


@router.get("/secrets", response_model=Page[SecretResponse], tags=["secrets"])
async def list_secrets(actor: CurrentActor) -> Page[SecretResponse]:
    await authorization.require_permission(actor, Permission.SECRET_MANAGE)
    views = await secret_service.list(actor.tenant_id)
    return Page[SecretResponse](
        items=[SecretResponse.model_validate(v, from_attributes=True) for v in views],
        total=len(views),
    )


@router.post("/secrets/{secret_id}/rotate", response_model=SecretResponse, tags=["secrets"])
async def rotate_secret(
    secret_id: uuid.UUID, payload: SecretRotateRequest, actor: CurrentActor
) -> SecretResponse:
    await authorization.require_permission(actor, Permission.SECRET_MANAGE)
    view = await secret_service.rotate(actor=actor, secret_id=secret_id, value=payload.value)
    return SecretResponse.model_validate(view, from_attributes=True)


@router.delete("/secrets/{secret_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["secrets"])
async def disable_secret(secret_id: uuid.UUID, actor: CurrentActor) -> None:
    await authorization.require_permission(actor, Permission.SECRET_MANAGE)
    await secret_service.disable(actor=actor, secret_id=secret_id)


# ------------------------------------------------------------------ 资源授权


@router.post(
    "/grants",
    response_model=GrantResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["grants"],
)
async def create_grant(payload: GrantCreateRequest, actor: CurrentActor) -> GrantResponse:
    await authorization.require_permission(actor, Permission.GRANT_MANAGE)
    grant = await grant_service.create(
        actor=actor,
        resource_type=payload.resource_type,
        resource_id=payload.resource_id,
        subject_type=payload.subject_type,
        subject_id=payload.subject_id,
        actions=payload.actions,
    )
    return GrantResponse.model_validate(grant)


@router.get("/grants", response_model=Page[GrantResponse], tags=["grants"])
async def list_grants(
    actor: CurrentActor, resource_type: str | None = None
) -> Page[GrantResponse]:
    await authorization.require_permission(actor, Permission.GRANT_MANAGE)
    grants = await grant_service.list(actor=actor, resource_type=resource_type)
    return Page[GrantResponse](
        items=[GrantResponse.model_validate(g) for g in grants], total=len(grants)
    )


@router.delete("/grants/{grant_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["grants"])
async def revoke_grant(grant_id: uuid.UUID, actor: CurrentActor) -> None:
    await authorization.require_permission(actor, Permission.GRANT_MANAGE)
    await grant_service.revoke(actor=actor, grant_id=grant_id)


# ------------------------------------------------------------------ 审计


@router.get("/audit-logs", response_model=Page[AuditLogResponse], tags=["audit"])
async def list_audit_logs(
    actor: CurrentActor,
    page: CurrentPage,
    resource_type: str | None = None,
    resource_id: uuid.UUID | None = None,
    action: str | None = None,
) -> Page[AuditLogResponse]:
    await authorization.require_permission(actor, Permission.AUDIT_READ)
    async with session_scope(actor.tenant_id) as session:
        entries = await audit_service.list_entries(
            session,
            tenant_id=actor.tenant_id,
            resource_type=resource_type,
            resource_id=resource_id,
            action=action,
            limit=page.limit,
            offset=page.offset,
        )
        total = await audit_service.count_entries(
            session,
            tenant_id=actor.tenant_id,
            resource_type=resource_type,
            resource_id=resource_id,
            action=action,
        )
    return Page[AuditLogResponse](
        items=[AuditLogResponse.model_validate(e) for e in entries], total=total
    )
