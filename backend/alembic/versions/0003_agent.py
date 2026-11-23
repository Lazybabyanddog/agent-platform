"""agent schema: Agent、不可变版本快照、版本绑定、发布记录与发布指针

Revision ID: 0003_agent
Revises: 0002_catalog
Create Date: 2026-11-23
"""

from __future__ import annotations

from alembic import op

from agent_platform.shared.metadata import metadata
from agent_platform.shared.rls import grant_app_role, tenant_policies

revision = "0003_agent"
down_revision = "0002_catalog"
branch_labels = None
depends_on = None

SCHEMA = "agent"

TABLES = [
    "agent",
    "agent_version",
    "agent_version_binding",
    "agent_release",
    "agent_release_pointer",
]

TENANT_TABLES = TABLES

# 快照、绑定与发布记录一经写入不可修改：应用角色没有 update/delete 权限
APPEND_ONLY_TABLES = [
    "agent_version",
    "agent_version_binding",
    "agent_release",
]


def upgrade() -> None:
    op.execute(f"create schema if not exists {SCHEMA}")
    bind = op.get_bind()
    metadata.create_all(bind, tables=[metadata.tables[f"{SCHEMA}.{name}"] for name in TABLES])

    for statement in grant_app_role(SCHEMA, append_only_tables=APPEND_ONLY_TABLES):
        op.execute(statement)

    for statement in tenant_policies(SCHEMA, TENANT_TABLES):
        op.execute(statement)


def downgrade() -> None:
    op.execute(f"drop schema if exists {SCHEMA} cascade")
