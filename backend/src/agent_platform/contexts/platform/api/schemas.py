from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from agent_platform.contexts.platform.domain.enums import (
    ApiKeyStatus,
    AuditResult,
    GrantSubjectType,
    TenantStatus,
)


class RegisterRequest(BaseModel):
    tenant_code: str = Field(min_length=2, max_length=64)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(min_length=1, max_length=120)


class UserCreatedResponse(BaseModel):
    user_id: uuid.UUID


class LoginRequest(BaseModel):
    tenant_code: str
    email: EmailStr
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    display_name: str
    tenant_id: uuid.UUID
    actor_id: uuid.UUID
    permissions: list[str]


class MeResponse(BaseModel):
    tenant_id: uuid.UUID
    actor_id: uuid.UUID
    actor_type: str
    display_name: str | None
    permissions: list[str]
    roles: list[str]


class TenantCreateRequest(BaseModel):
    code: str = Field(min_length=2, max_length=64, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None


class TenantStatusRequest(BaseModel):
    status: TenantStatus


class TenantResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    status: TenantStatus
    created_at: datetime


class MemberCreateRequest(BaseModel):
    user_id: uuid.UUID
    role_code: str = "member"
    department: str | None = None


class MemberResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    status: str
    department: str | None
    created_at: datetime


class ApiKeyCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    scopes: list[str] = Field(default_factory=list)
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)


class ApiKeyCreateResponse(BaseModel):
    id: uuid.UUID
    key_id: str
    name: str
    secret: str
    scopes: list[str]
    expires_at: datetime | None


class ApiKeyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    key_id: str
    name: str
    scopes: list[str]
    status: ApiKeyStatus
    expires_at: datetime | None
    last_used_at: datetime | None
    created_at: datetime


class SecretCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_.-]+$")
    value: str = Field(min_length=1)


class SecretRotateRequest(BaseModel):
    value: str = Field(min_length=1)


class SecretResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    backend: str
    status: str
    key_version: int
    masked: str
    created_at: datetime


class GrantCreateRequest(BaseModel):
    resource_type: str = Field(min_length=2, max_length=64)
    resource_id: uuid.UUID
    subject_type: GrantSubjectType
    subject_id: uuid.UUID
    actions: list[str] = Field(min_length=1)


class GrantResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    resource_type: str
    resource_id: uuid.UUID
    subject_type: GrantSubjectType
    subject_id: uuid.UUID
    actions: list[str]
    created_at: datetime


class AuditLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    actor_type: str
    actor_id: uuid.UUID | None
    action: str
    resource_type: str
    resource_id: uuid.UUID | None
    result: AuditResult
    detail: dict | None
    trace_id: str | None
    created_at: datetime


class Page[T](BaseModel):
    items: list[T]
    total: int
