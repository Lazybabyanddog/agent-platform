"""模型参数模板与取值校验。

模板里声明每个参数的取值范围；调用方传入的 params 必须落在范围内，
否则返回 422 并精确指出字段（而不是把非法值透传给上游模型）。

参数键的权威清单也在这里：适配器只透传采样参数，模板里出现别的键
（例如 ``reasoning_effort``）会在保存时被拒绝，而不是静默丢弃。
``timeout`` 是唯一不进入适配器 ``**kwargs`` 的键，由执行层单独取用。
"""

from __future__ import annotations

import re
from typing import Any

from agent_platform.settings import get_settings
from agent_platform.shared.errors import ValidationFailedError

TIMEOUT_PARAM = "timeout"

SAMPLING_PARAM_KEYS = frozenset(
    {
        "temperature",
        "max_tokens",
        "max_completion_tokens",
        "top_p",
        "presence_penalty",
        "frequency_penalty",
        "stop",
        "seed",
    }
)

# 平台受支持的参数键：采样参数 + 超时；与适配器白名单保持一一对应
SUPPORTED_MODEL_PARAMS = SAMPLING_PARAM_KEYS | {TIMEOUT_PARAM}

_TYPE_CHECKS: dict[str, tuple[type, ...]] = {
    "number": (int, float),
    "integer": (int,),
    "string": (str,),
    "boolean": (bool,),
}


def _timeout_limit() -> float:
    return float(get_settings().sync_max_timeout_s)


def _check_timeout(name: str, value: Any) -> None:
    """超时以秒为单位：必须大于零，且不超过平台同步调用上限。"""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValidationFailedError.for_field(
            f"params.{name}", f"expected number, got {type(value).__name__}"
        )
    if value <= 0:
        raise ValidationFailedError.for_field(f"params.{name}", "must be > 0")
    limit = _timeout_limit()
    if value > limit:
        raise ValidationFailedError.for_field(f"params.{name}", f"must be <= {limit:g}")


def _check_value(name: str, rule: dict[str, Any], value: Any) -> Any:
    expected = rule.get("type", "number")
    if expected == "integer" and isinstance(value, bool):
        raise ValidationFailedError.for_field(f"params.{name}", "expected integer, got boolean")
    if expected == "number" and isinstance(value, bool):
        raise ValidationFailedError.for_field(f"params.{name}", "expected number, got boolean")
    allowed_types = _TYPE_CHECKS.get(expected)
    if allowed_types is not None and not isinstance(value, allowed_types):
        raise ValidationFailedError.for_field(
            f"params.{name}", f"expected {expected}, got {type(value).__name__}"
        )
    if expected == "integer" and isinstance(value, float):
        raise ValidationFailedError.for_field(f"params.{name}", "expected integer, got float")

    if "enum" in rule and value not in rule["enum"]:
        raise ValidationFailedError.for_field(
            f"params.{name}", f"must be one of {rule['enum']}"
        )

    minimum = rule.get("min")
    maximum = rule.get("max")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if minimum is not None and value < minimum:
            raise ValidationFailedError.for_field(f"params.{name}", f"must be >= {minimum}")
        if maximum is not None and value > maximum:
            raise ValidationFailedError.for_field(f"params.{name}", f"must be <= {maximum}")
    if name == TIMEOUT_PARAM:
        _check_timeout(name, value)
    return value


_RULE_KEYS = {"type", "min", "max", "default", "enum", "required", "description"}
_RULE_TYPES = {"number", "integer", "string", "boolean"}


def validate_template(template: dict[str, Any]) -> None:
    """模板自身必须合法，否则后续所有调用都会失败。"""
    if not isinstance(template, dict):
        raise ValidationFailedError.for_field("param_template", "must be an object")
    for name, rule in template.items():
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise ValidationFailedError.for_field(
                f"param_template.{name}", "parameter name must be a valid identifier"
            )
        if not isinstance(rule, dict):
            raise ValidationFailedError.for_field(f"param_template.{name}", "rule must be an object")
        if name not in SUPPORTED_MODEL_PARAMS:
            raise ValidationFailedError.for_field(
                f"param_template.{name}", f"unsupported parameter {name!r}"
            )
        unknown = set(rule) - _RULE_KEYS
        if unknown:
            raise ValidationFailedError.for_field(
                f"param_template.{name}", f"unsupported keys: {sorted(unknown)}"
            )
        rule_type = rule.get("type", "number")
        if rule_type not in _RULE_TYPES:
            raise ValidationFailedError.for_field(
                f"param_template.{name}.type", f"unsupported type {rule_type!r}"
            )
        minimum, maximum = rule.get("min"), rule.get("max")
        if minimum is not None and maximum is not None and minimum > maximum:
            raise ValidationFailedError.for_field(f"param_template.{name}", "min must be <= max")
        if name == TIMEOUT_PARAM:
            if rule_type not in {"number", "integer"}:
                raise ValidationFailedError.for_field(
                    f"param_template.{name}.type", "timeout must be numeric"
                )
            limit = _timeout_limit()
            if minimum is not None and minimum <= 0:
                raise ValidationFailedError.for_field(
                    f"param_template.{name}.min", "must be > 0"
                )
            if maximum is not None and maximum > limit:
                raise ValidationFailedError.for_field(
                    f"param_template.{name}.max", f"must be <= {limit:g}"
                )
        if "default" in rule:
            _check_value(name, rule, rule["default"])


def normalize_params(template: dict[str, Any], values: dict[str, Any] | None) -> dict[str, Any]:
    """按模板校验并补默认值；未知参数直接拒绝，避免静默丢弃用户意图。"""
    values = values or {}
    unknown = sorted(set(values) - set(template))
    if unknown:
        raise ValidationFailedError.for_field(
            f"params.{unknown[0]}", f"unknown parameter {unknown[0]!r}"
        )

    normalized: dict[str, Any] = {}
    for name, rule in template.items():
        rule = rule or {}
        if name in values:
            normalized[name] = _check_value(name, rule, values[name])
            continue
        if "default" in rule:
            normalized[name] = rule["default"]
            continue
        if rule.get("required", False):
            raise ValidationFailedError.for_field(f"params.{name}", "parameter is required")
    return normalized
