"""Agent 软删除：deleted_at 标记，编码唯一性改为只约束未删除行

Revision ID: 0011_agent_soft_delete
Revises: 0010_session_history
Create Date: 2026-11-24
"""

from __future__ import annotations

from alembic import op

revision = "0011_agent_soft_delete"
down_revision = "0010_session_history"
branch_labels = None
depends_on = None

SCHEMA = "agent"


def upgrade() -> None:
    # 全部用 if (not) exists：0003 的 create_all 在全新库上已经按最新 metadata 建好，
    # 这里对已升级过的库做增量补齐。
    op.execute(
        f"alter table {SCHEMA}.agent add column if not exists deleted_at timestamptz"
    )
    op.execute(
        f"create index if not exists ix_agent_deleted_at on {SCHEMA}.agent (deleted_at)"
    )
    op.execute(
        f"alter table {SCHEMA}.agent drop constraint if exists uq_agent_tenant_id_code"
    )
    op.execute(
        f"create unique index if not exists uq_agent_tenant_id_code_active "
        f"on {SCHEMA}.agent (tenant_id, code) where deleted_at is null"
    )


def downgrade() -> None:
    op.execute(f"drop index if exists {SCHEMA}.uq_agent_tenant_id_code_active")
    op.execute(f"drop index if exists {SCHEMA}.ix_agent_deleted_at")
    op.execute(f"alter table {SCHEMA}.agent drop column if exists deleted_at")
    op.execute(
        f"alter table {SCHEMA}.agent "
        f"add constraint uq_agent_tenant_id_code unique (tenant_id, code)"
    )
