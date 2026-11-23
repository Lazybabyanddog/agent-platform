"""可重复执行的引导脚本。

职责：写入内置权限点与角色、确保首个租户存在、确保租户管理员账号存在。
重复执行不产生重复数据，因此可以放进任何部署流水线里跑。

用法::

    python -m agent_platform.bootstrap

环境变量（均有默认值，见 ``docs/dev-setup.md``）：
``BOOTSTRAP_TENANT_CODE`` / ``BOOTSTRAP_TENANT_NAME`` / ``BOOTSTRAP_ADMIN_EMAIL``
/ ``BOOTSTRAP_ADMIN_PASSWORD`` / ``BOOTSTRAP_ADMIN_NAME``。
"""

from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import dataclass

from agent_platform.contexts.platform.application.defaults import ensure_platform_defaults
from agent_platform.contexts.platform.domain.enums import MembershipStatus
from agent_platform.contexts.platform.domain.permissions import ROLE_ADMIN
from agent_platform.contexts.platform.infrastructure.repositories import (
    AuthorizationRepository,
    MembershipRepository,
    TenantRepository,
    UserRepository,
)
from agent_platform.shared.db import dispose_engines, session_scope, system_session_scope
from agent_platform.shared.security import hash_password


@dataclass(frozen=True, slots=True)
class BootstrapResult:
    tenant_id: uuid.UUID
    tenant_code: str
    user_id: uuid.UUID
    email: str
    created_tenant: bool
    created_user: bool
    role_assigned: bool


async def bootstrap(
    *,
    tenant_code: str,
    tenant_name: str,
    admin_email: str,
    admin_password: str,
    admin_display_name: str | None = None,
) -> BootstrapResult:
    async with system_session_scope() as session:
        await ensure_platform_defaults(session)

        tenants = TenantRepository()
        tenant = await tenants.by_code(session, tenant_code)
        created_tenant = tenant is None
        if tenant is None:
            tenant = await tenants.create(session, code=tenant_code, name=tenant_name)

        users = UserRepository()
        user = await users.by_email(session, admin_email)
        created_user = user is None
        if user is None:
            user = await users.create(
                session,
                email=admin_email,
                password_hash=hash_password(admin_password),
                display_name=admin_display_name or admin_email.split("@", 1)[0],
            )
        tenant_id, user_id = tenant.id, user.id

    role_assigned = False
    async with session_scope(tenant_id) as session:
        memberships = MembershipRepository()
        membership = await memberships.get(session, tenant_id=tenant_id, user_id=user_id)
        if membership is None:
            await memberships.create(session, tenant_id=tenant_id, user_id=user_id)
        elif membership.status != MembershipStatus.ACTIVE:
            membership.status = MembershipStatus.ACTIVE

        authz = AuthorizationRepository()
        role = await authz.role_by_code(session, tenant_id=tenant_id, code=ROLE_ADMIN)
        if role is not None:
            existing = await authz.role_ids_for_user(
                session, tenant_id=tenant_id, user_id=user_id
            )
            role_assigned = role.id not in existing
            await authz.assign_role(
                session, tenant_id=tenant_id, user_id=user_id, role_id=role.id
            )

    return BootstrapResult(
        tenant_id=tenant_id,
        tenant_code=tenant_code,
        user_id=user_id,
        email=admin_email.lower(),
        created_tenant=created_tenant,
        created_user=created_user,
        role_assigned=role_assigned,
    )


async def _main() -> None:
    result = await bootstrap(
        tenant_code=os.getenv("BOOTSTRAP_TENANT_CODE", "default"),
        tenant_name=os.getenv("BOOTSTRAP_TENANT_NAME", "Default Tenant"),
        admin_email=os.getenv("BOOTSTRAP_ADMIN_EMAIL", "admin@example.com"),
        admin_password=os.getenv("BOOTSTRAP_ADMIN_PASSWORD", "ChangeMe123!"),
        admin_display_name=os.getenv("BOOTSTRAP_ADMIN_NAME"),
    )
    print(
        "bootstrap ok: "
        f"tenant={result.tenant_code}({result.tenant_id}) "
        f"admin={result.email}({result.user_id}) "
        f"created_tenant={result.created_tenant} "
        f"created_user={result.created_user} "
        f"role_assigned={result.role_assigned}"
    )
    await dispose_engines()


if __name__ == "__main__":
    asyncio.run(_main())
