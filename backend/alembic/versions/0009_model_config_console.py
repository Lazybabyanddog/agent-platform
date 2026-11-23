"""模型配置台：供应商区域字段与连通性探测历史表

Revision ID: 0009_model_config_console
Revises: 0008_console_backfill
Create Date: 2026-11-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from agent_platform.shared.metadata import metadata
from agent_platform.shared.rls import grant_app_role, tenant_policies

revision = "0009_model_config_console"
down_revision = "0008_console_backfill"
branch_labels = None
depends_on = None

SCHEMA = "catalog"


def upgrade() -> None:
    op.add_column(
        "model_provider",
        sa.Column("region", sa.String(length=32), nullable=True),
        schema=SCHEMA,
    )

    bind = op.get_bind()
    metadata.create_all(bind, tables=[metadata.tables[f"{SCHEMA}.model_probe"]])

    for statement in grant_app_role(SCHEMA):
        op.execute(statement)
    for statement in tenant_policies(SCHEMA, ["model_probe"]):
        op.execute(statement)


def downgrade() -> None:
    op.drop_table("model_probe", schema=SCHEMA)
    op.drop_column("model_provider", "region", schema=SCHEMA)
