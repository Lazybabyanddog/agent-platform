"""agent console：执行会话表与 run 的预览支持

Revision ID: 0007_console
Revises: 0006_tool_call_tool_index
Create Date: 2026-11-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from agent_platform.shared.metadata import metadata
from agent_platform.shared.rls import grant_app_role, tenant_policies

revision = "0007_console"
down_revision = "0006_tool_call_tool_index"
branch_labels = None
depends_on = None

SCHEMA = "execution"

RUN_BINDING_CHECK = (
    "(kind = 'INVOKE' AND agent_release_id IS NOT NULL AND agent_version_id IS NOT NULL"
    " AND preview_snapshot IS NULL)"
    " OR (kind = 'PREVIEW' AND agent_release_id IS NULL AND agent_version_id IS NULL"
    " AND preview_snapshot IS NOT NULL)"
)


def upgrade() -> None:
    bind = op.get_bind()
    metadata.create_all(bind, tables=[metadata.tables[f"{SCHEMA}.execution_session"]])

    op.add_column(
        "run",
        sa.Column("kind", sa.String(length=16), nullable=False, server_default="INVOKE"),
        schema=SCHEMA,
    )
    op.add_column(
        "run",
        sa.Column("execution_session_id", sa.Uuid(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "run",
        sa.Column("preview_snapshot", postgresql.JSONB(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column("run", sa.Column("config_hash", sa.String(length=64), nullable=True), schema=SCHEMA)
    op.alter_column(
        "run", "agent_release_id", existing_type=sa.Uuid(), nullable=True, schema=SCHEMA
    )
    op.alter_column(
        "run", "agent_version_id", existing_type=sa.Uuid(), nullable=True, schema=SCHEMA
    )

    op.create_foreign_key(
        "fk_run_execution_session_id_execution_session",
        "run",
        "execution_session",
        ["execution_session_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="SET NULL",
    )
    op.execute(
        f"alter table {SCHEMA}.run add constraint ck_run_run_binding_matches_kind "
        f"check ({RUN_BINDING_CHECK})"
    )
    op.create_index(
        "ix_run_tenant_agent_kind_started",
        "run",
        ["tenant_id", "agent_id", "kind", "started_at"],
        schema=SCHEMA,
    )

    for statement in grant_app_role(SCHEMA):
        op.execute(statement)
    for statement in tenant_policies(SCHEMA, ["execution_session"]):
        op.execute(statement)


def downgrade() -> None:
    op.drop_index("ix_run_tenant_agent_kind_started", table_name="run", schema=SCHEMA)
    op.execute(f"alter table {SCHEMA}.run drop constraint if exists ck_run_run_binding_matches_kind")
    op.drop_constraint(
        "fk_run_execution_session_id_execution_session", "run", type_="foreignkey", schema=SCHEMA
    )
    op.drop_column("run", "config_hash", schema=SCHEMA)
    op.drop_column("run", "preview_snapshot", schema=SCHEMA)
    op.drop_column("run", "execution_session_id", schema=SCHEMA)
    op.drop_column("run", "kind", schema=SCHEMA)
    # 回退前需确认没有 PREVIEW 运行，否则这两个外键无法恢复非空
    op.alter_column(
        "run", "agent_version_id", existing_type=sa.Uuid(), nullable=False, schema=SCHEMA
    )
    op.alter_column(
        "run", "agent_release_id", existing_type=sa.Uuid(), nullable=False, schema=SCHEMA
    )
    op.drop_table("execution_session", schema=SCHEMA)
