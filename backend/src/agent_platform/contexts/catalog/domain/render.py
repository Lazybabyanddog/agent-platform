"""Prompt 模板渲染。

规则：
- 占位符语法 ``{{ name }}``；
- 只替换变量声明里出现过的名字，模板里出现未声明的占位符视为模板错误（不返回半成品）；
- 变量缺失或类型不符时整次渲染失败，由调用方拿到 422。
"""

from __future__ import annotations

import re
from typing import Any

from agent_platform.shared.errors import ValidationFailedError

_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")

_VALID_ROLES = {"system", "user", "assistant"}


def _coerce(name: str, declared: dict[str, Any], value: Any) -> str:
    expected = declared.get("type", "string")
    if expected == "string":
        if not isinstance(value, str):
            raise ValidationFailedError.for_field(f"variables.{name}", "expected string")
        return value
    if expected == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValidationFailedError.for_field(f"variables.{name}", "expected integer")
        return str(value)
    if expected == "number":
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValidationFailedError.for_field(f"variables.{name}", "expected number")
        return str(value)
    if expected == "boolean":
        if not isinstance(value, bool):
            raise ValidationFailedError.for_field(f"variables.{name}", "expected boolean")
        return "true" if value else "false"
    return str(value)


def _resolve_values(declarations: list[dict[str, Any]], values: dict[str, Any] | None) -> dict[str, str]:
    values = values or {}
    declared = {item["name"]: item for item in declarations}
    unknown = sorted(set(values) - set(declared))
    if unknown:
        raise ValidationFailedError.for_field(
            f"variables.{unknown[0]}", f"unknown variable {unknown[0]!r}"
        )

    resolved: dict[str, str] = {}
    for name, item in declared.items():
        if name in values:
            resolved[name] = _coerce(name, item, values[name])
        elif "default" in item and item["default"] is not None:
            resolved[name] = _coerce(name, item, item["default"])
        elif item.get("required", False):
            raise ValidationFailedError.for_field(f"variables.{name}", "variable is required")
        else:
            resolved[name] = ""
    return resolved


_VARIABLE_TYPES = {"string", "number", "integer", "boolean"}


def validate_messages_variables(
    messages: list[dict[str, Any]], variables: list[dict[str, Any]]
) -> None:
    """写入版本前做结构校验，保证存下来的模板一定可渲染。"""
    if not messages:
        raise ValidationFailedError.for_field("messages", "at least one message is required")

    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            raise ValidationFailedError.for_field(f"messages.{index}", "must be an object")
        if message.get("role") not in _VALID_ROLES:
            raise ValidationFailedError.for_field(f"messages.{index}.role", "unsupported role")
        content = message.get("content")
        if not isinstance(content, str):
            raise ValidationFailedError.for_field(f"messages.{index}.content", "must be a string")

    seen: set[str] = set()
    for index, variable in enumerate(variables):
        if not isinstance(variable, dict):
            raise ValidationFailedError.for_field(f"variables.{index}", "must be an object")
        name = variable.get("name")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise ValidationFailedError.for_field(f"variables.{index}.name", "invalid name")
        if name in seen:
            raise ValidationFailedError.for_field(f"variables.{index}.name", "duplicate name")
        seen.add(name)
        if variable.get("type", "string") not in _VARIABLE_TYPES:
            raise ValidationFailedError.for_field(f"variables.{index}.type", "unsupported type")
        if not isinstance(variable.get("required", False), bool):
            raise ValidationFailedError.for_field(
                f"variables.{index}.required", "must be a boolean"
            )

    declared = {item["name"] for item in variables}
    for index, message in enumerate(messages):
        for placeholder in _PLACEHOLDER.findall(message["content"]):
            if placeholder not in declared:
                raise ValidationFailedError.for_field(
                    f"messages.{index}.content", f"undeclared placeholder {placeholder!r}"
                )


def render_messages(
    messages: list[dict[str, Any]],
    variables: list[dict[str, Any]],
    values: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    resolved = _resolve_values(variables, values)
    declared = {item["name"] for item in variables}

    rendered: list[dict[str, str]] = []
    for index, message in enumerate(messages):
        role = message.get("role", "user")
        if role not in _VALID_ROLES:
            raise ValidationFailedError.for_field(f"messages.{index}.role", f"unsupported role {role!r}")
        content = message.get("content", "")

        for placeholder in _PLACEHOLDER.findall(content):
            if placeholder not in declared:
                raise ValidationFailedError.for_field(
                    f"messages.{index}.content", f"undeclared placeholder {placeholder!r}"
                )

        rendered.append(
            {"role": role, "content": _PLACEHOLDER.sub(lambda m: resolved[m.group(1)], content)}
        )
    return rendered
