"""只读扫描存量模型的参数模板（迁移 0009 上线后执行）。

用法：conda run -n agent-platform python scripts/scan_model_param_templates.py

输出两类清单，均带租户、模型 code 与具体键名：
1. 模板里用到了白名单之外的键 —— 保存/装配会被拒绝，需要人工清理；
2. 模板里已包含 timeout —— 该值从本次起真正生效，需要复核是否合理。
退出码固定为 0：扫描结果供人工处理，不作为流水线门禁。
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

import asyncpg

from agent_platform.contexts.catalog.domain.params import SUPPORTED_MODEL_PARAMS, TIMEOUT_PARAM
from agent_platform.settings import get_settings


def _pg_dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://")


def _as_template(raw: Any) -> dict[str, Any] | None:
    """兼容历史数据里被二次编码成字符串的模板。"""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return None
    return raw if isinstance(raw, dict) else None


async def _scan(conn: asyncpg.Connection) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    tenants = await conn.fetch("select id, code from platform.tenant order by code")
    unsupported: list[dict[str, Any]] = []
    with_timeout: list[dict[str, Any]] = []

    for tenant in tenants:
        await conn.execute("select set_config('app.tenant_id', $1, false)", str(tenant["id"]))
        models = await conn.fetch(
            "select code, model_name, param_template from catalog.model order by code"
        )
        for model in models:
            row = {
                "tenant": tenant["code"],
                "model": model["code"],
                "display_name": model["model_name"],
            }
            template = _as_template(model["param_template"])
            if template is None:
                unsupported.append({**row, "keys": ["<unparsable template>"]})
                continue
            keys = sorted(set(template))
            extra = [key for key in keys if key not in SUPPORTED_MODEL_PARAMS]
            if extra:
                unsupported.append({**row, "keys": extra})
            if TIMEOUT_PARAM in template:
                with_timeout.append({**row, "timeout": template[TIMEOUT_PARAM]})

    return unsupported, with_timeout


async def main() -> int:
    settings = get_settings()
    conn = await asyncpg.connect(dsn=_pg_dsn(settings.database_url), timeout=10)
    try:
        unsupported, with_timeout = await _scan(conn)
    finally:
        await conn.close()

    print(f"[1] 模板含白名单外键的模型：{len(unsupported)} 个")
    for item in unsupported:
        print(f"    - {item['tenant']} / {item['model']} ({item['display_name']}): {item['keys']}")

    print(f"[2] 模板已含 timeout 的模型：{len(with_timeout)} 个")
    for item in with_timeout:
        print(f"    - {item['tenant']} / {item['model']} ({item['display_name']}): {item['timeout']}")

    if not unsupported and not with_timeout:
        print("无需人工处理。")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
