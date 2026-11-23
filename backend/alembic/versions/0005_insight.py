"""insight schema: llm_call、tool_call 与正文留存开关

Revision ID: 0005_insight
Revises: 0004_execution
Create Date: 2026-11-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from agent_platform.shared.metadata import metadata
from agent_platform.shared.rls import grant_app_role, tenant_policies

revision = "0005_insight"
down_revision = "0004_execution"
branch_labels = None
depends_on = None

SCHEMA = "insight"

TABLES = ["llm_call", "tool_call"]

# 调用记录只追加，不允许改写历史轨迹
APPEND_ONLY_TABLES = TABLES


def upgrade() -> None:
    op.add_column(
        "tenant",
        sa.Column(
            "content_retention_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        schema="platform",
    )

    op.execute(f"create schema if not exists {SCHEMA}")
    bind = op.get_bind()
    metadata.create_all(bind, tables=[metadata.tables[f"{SCHEMA}.{name}"] for name in TABLES])

    for statement in grant_app_role(SCHEMA, append_only_tables=APPEND_ONLY_TABLES):
        op.execute(statement)

    for statement in tenant_policies(SCHEMA, TABLES):
        op.execute(statement)


def downgrade() -> None:
    op.execute(f"drop schema if exists {SCHEMA} cascade")
    op.drop_column("tenant", "content_retention_enabled", schema="platform")
