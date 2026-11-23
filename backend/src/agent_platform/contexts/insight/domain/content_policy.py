"""调用记录的内容策略：摘要、摘要指纹与敏感字段掩码。

默认不落正文，只保存结构摘要（键、长度、指纹），既能定位问题又不泄露内容；
租户显式开启正文留存后，正文仍需先做敏感字段掩码再落库。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

MASK = "***"

SENSITIVE_KEY_HINTS = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "private_key",
)


def is_sensitive_key(name: str, schema: dict[str, Any] | None = None) -> bool:
    if isinstance(schema, dict) and schema.get("x-sensitive") is True:
        return True
    lowered = name.lower()
    return any(hint in lowered for hint in SENSITIVE_KEY_HINTS)


def mask_sensitive(value: Any, schema: dict[str, Any] | None = None) -> Any:
    """按 JSON Schema 的 x-sensitive 标记与字段名约定递归掩码。"""
    if isinstance(value, dict):
        properties = (schema or {}).get("properties") or {}
        masked: dict[str, Any] = {}
        for key, item in value.items():
            child = properties.get(key)
            if is_sensitive_key(str(key), child):
                masked[key] = MASK
            else:
                masked[key] = mask_sensitive(item, child)
        return masked
    if isinstance(value, list):
        items = (schema or {}).get("items")
        return [mask_sensitive(item, items) for item in value]
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))


def digest_payload(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def summarize_text(value: str | None) -> dict[str, Any]:
    text = value or ""
    return {"chars": len(text), "digest": digest_payload(text)}


def summarize_messages(messages: list[dict[str, Any]]) -> dict[str, Any]:
    normalized = [
        {"role": str(item.get("role") or ""), "content": str(item.get("content") or "")}
        for item in messages
    ]
    return {
        "message_count": len(normalized),
        "chars": sum(len(item["content"]) for item in normalized),
        "digest": digest_payload(normalized),
    }


def summarize_arguments(arguments: dict[str, Any], schema: dict[str, Any] | None) -> dict[str, Any]:
    payload = mask_sensitive(arguments, schema)
    return {
        "keys": sorted(str(key) for key in payload),
        "chars": len(canonical_json(payload)),
        "digest": digest_payload(payload),
    }
