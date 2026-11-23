"""insight.tool_call 按工具查询索引

Revision ID: 0006_tool_call_tool_index
Revises: 0005_insight
Create Date: 2026-11-23
"""

from __future__ import annotations

from alembic import op

revision = "0006_tool_call_tool_index"
down_revision = "0005_insight"
branch_labels = None
depends_on = None

SCHEMA = "insight"
INDEX_NAME = "ix_tool_call_tenant_tool_started"


def upgrade() -> None:
    op.create_index(
        INDEX_NAME,
        "tool_call",
        ["tenant_id", "tool_id", "started_at"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index(INDEX_NAME, table_name="tool_call", schema=SCHEMA)
