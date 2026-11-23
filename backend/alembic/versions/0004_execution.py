"""execution schema: run、execution_event、checkpoint_ref

Revision ID: 0004_execution
Revises: 0003_agent
Create Date: 2026-11-23
"""

from __future__ import annotations

from alembic import op

from agent_platform.shared.metadata import metadata
from agent_platform.shared.rls import grant_app_role, tenant_policies

revision = "0004_execution"
down_revision = "0003_agent"
branch_labels = None
depends_on = None

SCHEMA = "execution"

TABLES = ["run", "execution_event", "checkpoint_ref"]

TENANT_TABLES = TABLES

# 执行事件与 checkpoint 引用只追加
APPEND_ONLY_TABLES = ["execution_event", "checkpoint_ref"]


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
