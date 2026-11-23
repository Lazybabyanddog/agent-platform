"""catalog schema: 供应商/模型、Prompt 与版本、工具与版本、工具授权

Revision ID: 0002_catalog
Revises: 0001_platform
Create Date: 2026-11-22
"""

from __future__ import annotations

from alembic import op

from agent_platform.shared.metadata import metadata
from agent_platform.shared.rls import grant_app_role, tenant_policies

revision = "0002_catalog"
down_revision = "0001_platform"
branch_labels = None
depends_on = None

SCHEMA = "catalog"

TABLES = [
    "model_provider",
    "model",
    "prompt",
    "prompt_version",
    "tool",
    "tool_version",
    "tool_grant",
]

TENANT_TABLES = [
    "model",
    "prompt",
    "prompt_version",
    "tool",
    "tool_version",
    "tool_grant",
]


def upgrade() -> None:
    op.execute(f"create schema if not exists {SCHEMA}")
    bind = op.get_bind()
    metadata.create_all(bind, tables=[metadata.tables[f"{SCHEMA}.{name}"] for name in TABLES])

    for statement in grant_app_role(SCHEMA):
        op.execute(statement)

    # 供应商允许平台级共享行（tenant_id IS NULL）
    for statement in tenant_policies(SCHEMA, ["model_provider"], allow_platform_rows=True):
        op.execute(statement)
    for statement in tenant_policies(SCHEMA, TENANT_TABLES):
        op.execute(statement)


def downgrade() -> None:
    op.execute(f"drop schema if exists {SCHEMA} cascade")
