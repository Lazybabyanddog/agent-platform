from __future__ import annotations

from enum import StrEnum


class TenantStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"


class UserStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class MembershipStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class ApiKeyStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class SecretStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class AuditResult(StrEnum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"


class GrantSubjectType(StrEnum):
    USER = "USER"
    ROLE = "ROLE"
    TENANT = "TENANT"


class ActorType(StrEnum):
    USER = "USER"
    API_KEY = "API_KEY"
    SYSTEM = "SYSTEM"


class OutboxStatus(StrEnum):
    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"
