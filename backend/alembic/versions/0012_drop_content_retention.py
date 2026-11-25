"""移除租户级正文留存开关

Revision ID: 0012_drop_content_retention
Revises: 0011_agent_soft_delete
Create Date: 2026-11-24
"""

from __future__ import annotations

from alembic import op

revision = "0012_drop_content_retention"
down_revision = "0011_agent_soft_delete"
branch_labels = None
depends_on = None

SCHEMA = "platform"


def upgrade() -> None:
    # 全新库由 0003 的 create_all 按最新 metadata 建表，已无此列；这里只做增量清理
    op.execute(
        f"alter table {SCHEMA}.tenant drop column if exists content_retention_enabled"
    )


def downgrade() -> None:
    op.execute(
        f"alter table {SCHEMA}.tenant "
        f"add column if not exists content_retention_enabled boolean not null default false"
    )
