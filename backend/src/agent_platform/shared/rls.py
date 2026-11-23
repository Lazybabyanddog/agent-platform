"""租户行级安全的 DDL 生成助手，供 Alembic 迁移复用。

策略统一使用 nullif(..., '') 处理"未注入租户"的情形：此时比较结果为 NULL，
查询返回空集，而不是报类型错误，也不是全量放行。
"""

from __future__ import annotations

TENANT_PREDICATE = "tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid"
TENANT_OR_PLATFORM_PREDICATE = (
    "(tenant_id IS NULL OR tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)"
)


def tenant_policies(schema: str, tables: list[str], *, allow_platform_rows: bool = False) -> list[str]:
    """为租户级表开启并强制 RLS，返回需要执行的语句。"""
    predicate = TENANT_OR_PLATFORM_PREDICATE if allow_platform_rows else TENANT_PREDICATE
    statements: list[str] = []
    for table in tables:
        qualified = f"{schema}.{table}"
        statements.append(f"alter table {qualified} enable row level security")
        statements.append(f"alter table {qualified} force row level security")
        statements.append(
            f"create policy tenant_isolation on {qualified} using ({predicate}) with check ({predicate})"
        )
    return statements


def grant_app_role(schema: str, *, append_only_tables: list[str] | None = None) -> list[str]:
    """把 schema 的读写权限授予应用角色，并对只追加表收回写权限。"""
    statements = [
        f"grant usage on schema {schema} to agent_app",
        f"grant select, insert, update, delete on all tables in schema {schema} to agent_app",
        f"grant usage, select on all sequences in schema {schema} to agent_app",
        (
            f"alter default privileges in schema {schema} "
            "grant select, insert, update, delete on tables to agent_app"
        ),
    ]
    for table in append_only_tables or []:
        statements.append(f"revoke update, delete on {schema}.{table} from agent_app")
    return statements
