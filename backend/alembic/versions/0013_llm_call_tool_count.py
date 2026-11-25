"""模型调用记录新增工具数量

Revision ID: 0013_llm_call_tool_count
Revises: 0012_drop_content_retention
Create Date: 2026-11-24
"""

from __future__ import annotations

from alembic import op

revision = "0013_llm_call_tool_count"
down_revision = "0012_drop_content_retention"
branch_labels = None
depends_on = None

SCHEMA = "insight"


def upgrade() -> None:
    # 可空：历史记录无法回填（当时没有记录工具数量），
    # 0 与 NULL 的区别是"确实没带工具"与"当时未记录"
    op.execute(f"alter table {SCHEMA}.llm_call add column if not exists tool_count integer")


def downgrade() -> None:
    op.execute(f"alter table {SCHEMA}.llm_call drop column if exists tool_count")
