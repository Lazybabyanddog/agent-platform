"""清理调试台存量会话历史（一次性运维脚本）。

删除范围：会话元数据、运行记录、会话线程 checkpoint、追踪记录。
默认 dry-run 只打印各表待删行数；加 --yes 才真正执行。

用法::

    python scripts/purge_session_history.py                # 全库 dry-run
    python scripts/purge_session_history.py --tenant acme   # 只统计某个租户
    python scripts/purge_session_history.py --yes          # 执行删除

执行前请确认已备份：删除不可逆，且 insight.llm_call / insight.tool_call
未授予应用角色 DELETE 权限，脚本以平台级连接执行。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass

from sqlalchemy import text

from agent_platform.shared.db import dispose_engines, system_session_scope


@dataclass(frozen=True)
class Step:
    label: str
    table: str
    predicate: str


THREADS_ALL = "thread_id like 'sess-%'"
THREADS_BY_TENANT = (
    "thread_id in ("
    "select 'sess-' || id::text from execution.execution_session where tenant_id = :tid"
    ")"
)


def _steps(tenant_id: str | None) -> list[Step]:
    threads = THREADS_BY_TENANT if tenant_id else THREADS_ALL
    return [
        # checkpoint 三表没有外键，按会话线程前缀清理
        Step("checkpoint_writes", "agent.checkpoint_writes", threads),
        Step("checkpoint_blobs", "agent.checkpoint_blobs", threads),
        Step("checkpoints", "agent.checkpoints", threads),
        # 追踪表不随 run 级联
        Step("tool_call", "insight.tool_call", "tenant_id = :tid"),
        Step("llm_call", "insight.llm_call", "tenant_id = :tid"),
        # 删 run 会级联 checkpoint_ref 与 execution_event
        Step("run", "execution.run", "tenant_id = :tid"),
        Step("execution_session", "execution.execution_session", "tenant_id = :tid"),
    ]


async def _tenant_ids() -> list[str]:
    async with system_session_scope() as session:
        rows = await session.scalars(
            text("select id::text from platform.tenant order by created_at")
        )
        return list(rows)


async def _tenant_id_by_code(code: str) -> str:
    async with system_session_scope() as session:
        tenant_id = await session.scalar(
            text("select id::text from platform.tenant where code = :code"),
            {"code": code},
        )
    if tenant_id is None:
        raise SystemExit(f"租户不存在: {code}")
    return tenant_id


async def _run_tenant(tenant_id: str, *, apply: bool) -> int:
    params = {"tid": tenant_id}
    total = 0
    async with system_session_scope() as session:
        for step in _steps(tenant_id):
            if apply:
                result = await session.execute(
                    text(f"delete from {step.table} where {step.predicate}"), params
                )
                count = int(result.rowcount or 0)
            else:
                count = int(
                    await session.scalar(
                        text(f"select count(*) from {step.table} where {step.predicate}"),
                        params,
                    )
                    or 0
                )
            total += count
            print(f"  {step.label:<20} {count:>8}")
    return total


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="清理调试台存量会话历史")
    parser.add_argument("--yes", action="store_true", help="真正执行删除，默认只统计")
    parser.add_argument("--tenant", help="只处理指定租户编码")
    args = parser.parse_args(argv)

    tenant_ids = (
        [await _tenant_id_by_code(args.tenant)] if args.tenant else await _tenant_ids()
    )
    mode = "执行删除" if args.yes else "dry-run（不写库）"
    print(f"模式: {mode}；租户数: {len(tenant_ids)}")

    grand_total = 0
    for tenant_id in tenant_ids:
        print(f"tenant {tenant_id}")
        grand_total += await _run_tenant(tenant_id, apply=args.yes)

    print(f"合计 {grand_total} 行{'已删除' if args.yes else '待删除'}")
    if not args.yes:
        print("确认后加 --yes 执行。")
    return 0


async def _entry() -> int:
    try:
        return await main()
    finally:
        await dispose_engines()


if __name__ == "__main__":
    sys.exit(asyncio.run(_entry()))
