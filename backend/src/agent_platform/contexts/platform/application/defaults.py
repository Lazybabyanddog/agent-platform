"""平台内置数据：权限点与内置角色。幂等，可重复执行。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_platform.contexts.platform.domain.permissions import (
    BUILTIN_ROLE_PERMISSIONS,
    ROLE_ADMIN,
    ROLE_MEMBER,
    ROLE_PLATFORM_ADMIN,
)
from agent_platform.contexts.platform.infrastructure.tables import (
    Permission,
    Role,
    RolePermission,
)

PERMISSION_DEFS = {
    "tenant:manage": ("租户管理", "tenant", "manage"),
    "tenant:read": ("租户查看", "tenant", "read"),
    "agent:read": ("Agent 查看", "agent", "read"),
    "agent:manage": ("Agent 编辑", "agent", "manage"),
    "agent:publish": ("Agent 发布", "agent", "publish"),
    "agent:invoke": ("Agent 调用", "agent", "invoke"),
    "model:read": ("模型查看", "model", "read"),
    "model:manage": ("模型管理", "model", "manage"),
    "prompt:read": ("Prompt 查看", "prompt", "read"),
    "prompt:manage": ("Prompt 管理", "prompt", "manage"),
    "tool:read": ("工具查看", "tool", "read"),
    "tool:manage": ("工具管理", "tool", "manage"),
    "secret:manage": ("凭据管理", "secret", "manage"),
    "apikey:manage": ("API Key 管理", "api_key", "manage"),
    "audit:read": ("审计查看", "audit", "read"),
    "grant:manage": ("资源授权管理", "grant", "manage"),
    "run:read": ("运行记录查看", "run", "read"),
    "run:delete": ("会话删除", "run", "delete"),
    "insight:read_content": ("轨迹内容查看", "insight", "read_content"),
}

BUILTIN_ROLES = {
    ROLE_PLATFORM_ADMIN: "平台管理员",
    ROLE_ADMIN: "租户管理员",
    ROLE_MEMBER: "成员",
}


async def ensure_platform_defaults(session: AsyncSession) -> None:
    existing_permissions = {p.code: p for p in (await session.scalars(select(Permission))).all()}
    for code, (name, resource_type, action) in PERMISSION_DEFS.items():
        if code not in existing_permissions:
            session.add(Permission(code=code, name=name, resource_type=resource_type, action=action))
    await session.flush()

    roles = {r.code: r for r in (await session.scalars(select(Role).where(Role.tenant_id.is_(None)))).all()}
    for code, name in BUILTIN_ROLES.items():
        if code not in roles:
            role = Role(tenant_id=None, code=code, name=name, builtin=True)
            session.add(role)
            await session.flush()
            roles[code] = role

    permissions = {p.code: p for p in (await session.scalars(select(Permission))).all()}
    existing_links = {
        (rp.role_id, rp.permission_id) for rp in (await session.scalars(select(RolePermission))).all()
    }

    for role_code, permission_codes in BUILTIN_ROLE_PERMISSIONS.items():
        role = roles.get(role_code)
        if role is None:
            continue
        for permission_code in permission_codes:
            permission = permissions.get(permission_code)
            if permission is None:
                continue
            if (role.id, permission.id) not in existing_links:
                session.add(RolePermission(role_id=role.id, permission_id=permission.id))
    await session.flush()
