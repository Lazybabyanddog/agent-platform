"""调试台会话历史：会话可辨识元数据与归属

Revision ID: 0010_session_history
Revises: 0009_model_config_console
Create Date: 2026-11-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010_session_history"
down_revision = "0009_model_config_console"
branch_labels = None
depends_on = None

SCHEMA = "execution"


def upgrade() -> None:
    op.add_column(
        "execution_session",
        sa.Column("title", sa.String(length=120), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "execution_session",
        sa.Column("turn_count", sa.Integer(), nullable=False, server_default="0"),
        schema=SCHEMA,
    )
    op.add_column(
        "execution_session",
        sa.Column("created_by", sa.Uuid(as_uuid=True), nullable=True),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_execution_session_tenant_agent_owner_active",
        "execution_session",
        ["tenant_id", "agent_id", "kind", "created_by", "last_active_at"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_execution_session_tenant_agent_owner_active",
        table_name="execution_session",
        schema=SCHEMA,
    )
    op.drop_column("execution_session", "created_by", schema=SCHEMA)
    op.drop_column("execution_session", "turn_count", schema=SCHEMA)
    op.drop_column("execution_session", "title", schema=SCHEMA)
