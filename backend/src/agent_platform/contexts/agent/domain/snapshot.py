"""Agent 版本快照的组装与策略校验。

快照是"执行时可完全还原"的冻结物：模型参数、Prompt 正文、工具契约都在里面，
执行不依赖任何会变的当前值，因此历史版本回放结果稳定。
"""

from __future__ import annotations

import uuid
from typing import Any

from agent_platform.contexts.agent.domain.enums import BindingResourceType, BindingRole
from agent_platform.shared.errors import ValidationFailedError
from agent_platform.shared.hashing import config_hash

SNAPSHOT_SCHEMA_VERSION = 1
MAX_STEPS_LIMIT = 20


def build_snapshot(
    *,
    agent_code: str,
    model: dict[str, Any],
    prompt: dict[str, Any],
    tools: list[dict[str, Any]],
    policy: dict[str, Any],
) -> dict[str, Any]:
    return {
        "snapshot_schema_version": SNAPSHOT_SCHEMA_VERSION,
        "agent_code": agent_code,
        "model": model,
        "prompt": prompt,
        "tools": tools,
        "policy": policy,
    }


def normalize_policy(policy: dict[str, Any] | None, *, max_timeout_s: int) -> dict[str, Any]:
    policy = policy or {}
    max_steps = policy.get("max_steps", 6)
    if isinstance(max_steps, bool) or not isinstance(max_steps, int):
        raise ValidationFailedError.for_field("policy.max_steps", "must be an integer")
    if not 1 <= max_steps <= MAX_STEPS_LIMIT:
        raise ValidationFailedError.for_field(
            "policy.max_steps", f"must be within [1, {MAX_STEPS_LIMIT}]"
        )

    timeout_s = policy.get("timeout_s", 30)
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, int | float):
        raise ValidationFailedError.for_field("policy.timeout_s", "must be a number")
    if not 0 < float(timeout_s) <= max_timeout_s:
        raise ValidationFailedError.for_field(
            "policy.timeout_s", f"must be within (0, {max_timeout_s}]"
        )

    token_budget = policy.get("token_budget", 8000)
    if isinstance(token_budget, bool) or not isinstance(token_budget, int):
        raise ValidationFailedError.for_field("policy.token_budget", "must be an integer")
    if token_budget < 1:
        raise ValidationFailedError.for_field("policy.token_budget", "must be positive")

    session_ttl_h = policy.get("session_ttl_h", 168)
    if isinstance(session_ttl_h, bool) or not isinstance(session_ttl_h, int):
        raise ValidationFailedError.for_field("policy.session_ttl_h", "must be an integer")
    if not 1 <= session_ttl_h <= 720:
        raise ValidationFailedError.for_field("policy.session_ttl_h", "must be within [1, 720]")

    history_token_limit = policy.get("history_token_limit", 4000)
    if isinstance(history_token_limit, bool) or not isinstance(history_token_limit, int):
        raise ValidationFailedError.for_field(
            "policy.history_token_limit", "must be an integer"
        )
    if not 256 <= history_token_limit <= 32000:
        raise ValidationFailedError.for_field(
            "policy.history_token_limit", "must be within [256, 32000]"
        )

    return {
        "max_steps": max_steps,
        "timeout_s": float(timeout_s),
        "token_budget": token_budget,
        "session_ttl_h": session_ttl_h,
        "history_token_limit": history_token_limit,
    }


def snapshot_reference_ids(snapshot: dict[str, Any]) -> dict[str, list[uuid.UUID]]:
    """从快照里取出需要在发布前复检的资源 id。"""
    tools = [uuid.UUID(str(item["tool_version_id"])) for item in snapshot.get("tools", [])]
    return {
        "model": [uuid.UUID(str(snapshot["model"]["model_id"]))],
        "prompt": [uuid.UUID(str(snapshot["prompt"]["prompt_version_id"]))],
        "tool": tools,
    }


def bindings_for_snapshot(
    *, agent_version_id: uuid.UUID, snapshot: dict[str, Any]
) -> list[dict[str, Any]]:
    """绑定行由快照推导，保存版本与复制版本共用同一套映射。"""
    model = snapshot["model"]
    prompt = snapshot["prompt"]
    bindings: list[dict[str, Any]] = [
        {
            "agent_version_id": agent_version_id,
            "role": BindingRole.MODEL,
            "resource_type": BindingResourceType.MODEL,
            "resource_id": uuid.UUID(str(model["model_id"])),
            "resource_version_id": None,
            "overrides": {"params": model.get("params") or {}},
        },
        {
            "agent_version_id": agent_version_id,
            "role": BindingRole.PROMPT,
            "resource_type": BindingResourceType.PROMPT_VERSION,
            "resource_id": uuid.UUID(str(prompt["prompt_id"])),
            "resource_version_id": uuid.UUID(str(prompt["prompt_version_id"])),
            "overrides": {},
        },
    ]
    for tool in snapshot.get("tools", []):
        bindings.append(
            {
                "agent_version_id": agent_version_id,
                "role": BindingRole.TOOL,
                "resource_type": BindingResourceType.TOOL_VERSION,
                "resource_id": uuid.UUID(str(tool["tool_id"])),
                "resource_version_id": uuid.UUID(str(tool["tool_version_id"])),
                "overrides": {},
            }
        )
    return bindings


def _model_view(snapshot: dict[str, Any]) -> dict[str, Any]:
    model = snapshot.get("model") or {}
    return {
        "model_id": model.get("model_id"),
        "code": model.get("code"),
        "model_name": model.get("model_name"),
        "params": model.get("params") or {},
    }


def _prompt_view(snapshot: dict[str, Any]) -> dict[str, Any]:
    prompt = snapshot.get("prompt") or {}
    return {
        "prompt_id": prompt.get("prompt_id"),
        "prompt_version_id": prompt.get("prompt_version_id"),
        "version_no": prompt.get("version_no"),
    }


def _tool_view(tool: dict[str, Any]) -> dict[str, Any]:
    return {
        "tool_id": tool.get("tool_id"),
        "tool_code": tool.get("tool_code"),
        "tool_version_id": tool.get("tool_version_id"),
        "version_no": tool.get("version_no"),
    }


def diff_snapshots(
    base: dict[str, Any], target: dict[str, Any]
) -> dict[str, Any]:
    """对比两个快照的资源绑定与运行策略，结果可直接对外返回。"""
    base_tools = {str(item.get("tool_version_id")): item for item in base.get("tools", [])}
    target_tools = {str(item.get("tool_version_id")): item for item in target.get("tools", [])}
    target_by_code = {str(item.get("tool_code")): item for item in target.get("tools", [])}
    base_by_code = {str(item.get("tool_code")): item for item in base.get("tools", [])}

    added = [
        _tool_view(tool)
        for key, tool in target_tools.items()
        if key not in base_tools
    ]
    removed = [
        _tool_view(tool)
        for key, tool in base_tools.items()
        if key not in target_tools
    ]
    changed = []
    for code in sorted(set(base_by_code) & set(target_by_code)):
        base_tool = base_by_code[code]
        target_tool = target_by_code[code]
        if base_tool.get("tool_version_id") != target_tool.get("tool_version_id"):
            changed.append(
                {
                    "tool_code": code,
                    "base": _tool_view(base_tool),
                    "target": _tool_view(target_tool),
                }
            )

    base_policy = base.get("policy") or {}
    target_policy = target.get("policy") or {}
    policy_changes = {
        key: {"base": base_policy.get(key), "target": target_policy.get(key)}
        for key in sorted(set(base_policy) | set(target_policy))
        if base_policy.get(key) != target_policy.get(key)
    }

    model_base = _model_view(base)
    model_target = _model_view(target)
    prompt_base = _prompt_view(base)
    prompt_target = _prompt_view(target)
    baseline = {
        "model": model_base,
        "prompt": prompt_base,
        "tools": sorted(base_tools),
        "policy": base_policy,
    }
    candidate = {
        "model": model_target,
        "prompt": prompt_target,
        "tools": sorted(target_tools),
        "policy": target_policy,
    }
    return {
        "identical": config_hash(baseline) == config_hash(candidate),
        "model": {
            "changed": model_base != model_target,
            "base": model_base,
            "target": model_target,
        },
        "prompt": {
            "changed": prompt_base != prompt_target,
            "base": prompt_base,
            "target": prompt_target,
        },
        "tools": {
            "added": added,
            "removed": removed,
            "changed": changed,
        },
        "policy": policy_changes,
    }
