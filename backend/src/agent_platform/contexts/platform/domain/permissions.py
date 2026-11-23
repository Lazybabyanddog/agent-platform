"""权限点常量。格式固定为 ``<resource_type>:<action>``。"""

from __future__ import annotations

from enum import StrEnum


class Permission(StrEnum):
    TENANT_MANAGE = "tenant:manage"
    TENANT_READ = "tenant:read"

    AGENT_READ = "agent:read"
    AGENT_MANAGE = "agent:manage"
    AGENT_PUBLISH = "agent:publish"
    AGENT_INVOKE = "agent:invoke"

    MODEL_READ = "model:read"
    MODEL_MANAGE = "model:manage"

    PROMPT_READ = "prompt:read"
    PROMPT_MANAGE = "prompt:manage"

    TOOL_READ = "tool:read"
    TOOL_MANAGE = "tool:manage"

    SECRET_MANAGE = "secret:manage"
    APIKEY_MANAGE = "apikey:manage"
    AUDIT_READ = "audit:read"
    GRANT_MANAGE = "grant:manage"

    RUN_READ = "run:read"
    INSIGHT_READ_CONTENT = "insight:read_content"


ROLE_ADMIN = "tenant_admin"
ROLE_PLATFORM_ADMIN = "platform_admin"
ROLE_MEMBER = "member"

# 内置角色 -> 权限点
BUILTIN_ROLE_PERMISSIONS: dict[str, list[str]] = {
    ROLE_ADMIN: [p.value for p in Permission],
    ROLE_MEMBER: [
        Permission.AGENT_READ.value,
        Permission.AGENT_INVOKE.value,
        Permission.MODEL_READ.value,
        Permission.PROMPT_READ.value,
        Permission.TOOL_READ.value,
        Permission.RUN_READ.value,
    ],
}
