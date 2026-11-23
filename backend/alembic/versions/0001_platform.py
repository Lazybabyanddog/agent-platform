"""platform schema: 租户、身份、RBAC 授权、密钥、审计、发件箱

Revision ID: 0001_platform
Revises:
Create Date: 2026-11-22
"""

from __future__ import annotations

from alembic import op

from agent_platform.shared.metadata import metadata
from agent_platform.shared.rls import grant_app_role, tenant_policies

revision = "0001_platform"
down_revision = None
branch_labels = None
depends_on = None

SCHEMA = "platform"

TABLES = [
    "tenant",
    "user_account",
    "membership",
    "role",
    "permission",
    "role_permission",
    "user_role",
    "resource_grant",
    "api_key",
    "secret_ref",
    "audit_log",
    "outbox_event",
]

TENANT_TABLES = [
    "membership",
    "user_role",
    "resource_grant",
    "api_key",
    "secret_ref",
    "audit_log",
    "outbox_event",
]

APPEND_ONLY_TABLES = ["audit_log"]


def upgrade() -> None:
    op.execute(f"create schema if not exists {SCHEMA}")
    bind = op.get_bind()
    metadata.create_all(bind, tables=[metadata.tables[f"{SCHEMA}.{name}"] for name in TABLES])

    for statement in grant_app_role(SCHEMA, append_only_tables=APPEND_ONLY_TABLES):
        op.execute(statement)

    # role 允许平台级行（tenant_id IS NULL），但应用角色只能写本租户的行
    for statement in tenant_policies(SCHEMA, TENANT_TABLES):
        op.execute(statement)
    for statement in tenant_policies(SCHEMA, ["role"], allow_platform_rows=True):
        op.execute(statement)


def downgrade() -> None:
    op.execute(f"drop schema if exists {SCHEMA} cascade")
