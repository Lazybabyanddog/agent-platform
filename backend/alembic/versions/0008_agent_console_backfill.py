"""agent console：回填运行种类与 Agent 状态

现存数据里发布流程不会推进状态，因此"已发布但仍为草稿"的 Agent 需要按
发布记录回填为 ACTIVE，否则停用/启用与调用前校验会与真实情况不符。

Revision ID: 0008_console_backfill
Revises: 0007_console
Create Date: 2026-11-23
"""

from __future__ import annotations

from alembic import op

revision = "0008_console_backfill"
down_revision = "0007_console"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("update execution.run set kind = 'INVOKE' where kind is null")
    op.execute(
        """
        update agent.agent a
           set status = 'ACTIVE'
         where a.status = 'DRAFT'
           and exists (select 1 from agent.agent_release r where r.agent_id = a.id)
        """
    )


def downgrade() -> None:
    # 数据回填是一次性的：把 ACTIVE 还原为 DRAFT 会把本次变更后正常启用的
    # Agent 一起降级，因此这里不回滚数据。
    pass
