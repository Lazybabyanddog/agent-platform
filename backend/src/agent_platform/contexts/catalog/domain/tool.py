"""工具定义的规格校验。

一个工具版本就是一份固定的调用契约：地址、鉴权方式、入参/出参结构、超时，
HTTP 工具额外声明方法与请求头，MCP 工具额外声明传输方式与远端工具名。
写入前把契约校验干净，执行时才能把"工具返回了非预期结构"当成可识别的错误而不是脏数据。
"""

from __future__ import annotations

import uuid
from typing import Any

from jsonschema import Draft202012Validator, SchemaError

from agent_platform.contexts.catalog.domain.enums import ToolType
from agent_platform.shared.errors import ValidationFailedError

HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
AUTH_TYPES = {"none", "bearer", "header"}
MAX_TIMEOUT_S = 60

# 只允许远程 HTTP 类传输：平台不托管远端进程，因此明确拒绝 stdio
MCP_TRANSPORTS = {"streamable_http", "sse"}
MAX_TOOL_NAME_LENGTH = 128
MAX_DESCRIPTION_LENGTH = 2000
# 返回语义目前只支持声明"哪些结果属于业务终态"
RETURN_KEYS = {"terminal_statuses"}
# 模型可见契约的注解类关键字：允许与调用契约不同，因为它们不改变校验行为
_DATE_PARAM_HINTS = ("date", "time", "day")


def validate_spec(spec: dict[str, Any], tool_type: ToolType = ToolType.HTTP) -> dict[str, Any]:
    """按接入类型分派契约校验，返回归一化后的 spec。"""
    if not isinstance(spec, dict):
        raise ValidationFailedError.for_field("spec", "must be an object")

    if tool_type == ToolType.MCP:
        return _validate_mcp(spec)
    return _validate_http(spec)


def _validate_http(spec: dict[str, Any]) -> dict[str, Any]:
    method = str(spec.get("method", "POST")).upper()
    if method not in HTTP_METHODS:
        raise ValidationFailedError.for_field("spec.method", f"unsupported method {method!r}")

    url = spec.get("url")
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        raise ValidationFailedError.for_field(
            "spec.url", "must be an absolute http(s) url"
        )

    timeout_s = _validate_timeout(spec)
    headers = _validate_headers(spec)
    auth = _validate_auth(spec)
    _validate_schemas(spec)
    _validate_model_contract(spec)

    return {
        **spec,
        "method": method,
        "timeout_s": timeout_s,
        "headers": headers,
        "auth": auth,
    }


def _validate_mcp(spec: dict[str, Any]) -> dict[str, Any]:
    endpoint = validate_mcp_endpoint(spec)

    tool_name = spec.get("tool_name")
    if not isinstance(tool_name, str) or not tool_name.strip():
        raise ValidationFailedError.for_field("spec.tool_name", "remote tool name is required")
    if len(tool_name) > MAX_TOOL_NAME_LENGTH:
        raise ValidationFailedError.for_field(
            "spec.tool_name", f"must be at most {MAX_TOOL_NAME_LENGTH} characters"
        )

    _validate_schemas(spec)
    _validate_model_contract(spec)

    return {**spec, **endpoint, "tool_name": tool_name}


def validate_mcp_endpoint(spec: dict[str, Any]) -> dict[str, Any]:
    """只校验「连到哪里、怎么连」：传输方式、地址、超时、请求头、鉴权。

    探测与保存共用这一段。探测发生在选定远端工具名之前，拿不到 tool_name；
    若两处各写一份端点规则，迟早漂移成「探测能连、保存被拒」。
    """
    transport = spec.get("transport", "streamable_http")
    if transport not in MCP_TRANSPORTS:
        raise ValidationFailedError.for_field(
            "spec.transport",
            f"unsupported transport {transport!r}, only {sorted(MCP_TRANSPORTS)} are allowed",
        )

    url = spec.get("url")
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        raise ValidationFailedError.for_field(
            "spec.url", "must be an absolute http(s) url"
        )

    timeout_s = _validate_timeout(spec)
    headers = _validate_headers(spec)
    auth = _validate_auth(spec)

    return {
        "transport": transport,
        "url": url,
        "timeout_s": timeout_s,
        "headers": headers,
        "auth": auth,
    }


def _validate_timeout(spec: dict[str, Any]) -> float:
    timeout_s = spec.get("timeout_s", 15)
    if not isinstance(timeout_s, int | float) or isinstance(timeout_s, bool):
        raise ValidationFailedError.for_field("spec.timeout_s", "must be a number")
    if not (0 < float(timeout_s) <= MAX_TIMEOUT_S):
        raise ValidationFailedError.for_field(
            "spec.timeout_s", f"must be within (0, {MAX_TIMEOUT_S}]"
        )
    return float(timeout_s)


def _validate_headers(spec: dict[str, Any]) -> dict[str, Any]:
    headers = spec.get("headers") or {}
    if not isinstance(headers, dict):
        raise ValidationFailedError.for_field("spec.headers", "must be an object")
    return headers


def _validate_auth(spec: dict[str, Any]) -> dict[str, Any]:
    auth = spec.get("auth") or {"type": "none"}
    if not isinstance(auth, dict) or auth.get("type", "none") not in AUTH_TYPES:
        raise ValidationFailedError.for_field("spec.auth.type", "unsupported auth type")
    if auth.get("type", "none") != "none":
        secret_ref_id = auth.get("secret_ref_id")
        try:
            uuid.UUID(str(secret_ref_id))
        except (TypeError, ValueError) as exc:
            raise ValidationFailedError.for_field(
                "spec.auth.secret_ref_id", "must be a secret id"
            ) from exc
        if auth["type"] == "header" and not auth.get("name"):
            raise ValidationFailedError.for_field("spec.auth.name", "header name is required")
    return auth


def _validate_schemas(spec: dict[str, Any]) -> None:
    for key in ("input_schema", "output_schema"):
        schema = spec.get(key)
        if schema is None:
            continue
        if not isinstance(schema, dict):
            raise ValidationFailedError.for_field(f"spec.{key}", "must be an object")
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as exc:
            raise ValidationFailedError.for_field(f"spec.{key}", exc.message) from exc
        if schema.get("type") not in (None, "object"):
            raise ValidationFailedError.for_field(f"spec.{key}.type", "must be 'object'")


def _validate_model_contract(spec: dict[str, Any]) -> None:
    """校验模型可见的那部分契约：用途描述、参数说明契约、返回语义。

    这三项不参与调用，只决定模型如何构造参数与解读结果，因此与调用契约分开校验：
    模型可见契约可以加注解，但不能改变"能传什么"。
    """
    description = spec.get("description")
    if description is not None:
        if not isinstance(description, str):
            raise ValidationFailedError.for_field("spec.description", "must be a string")
        if len(description) > MAX_DESCRIPTION_LENGTH:
            raise ValidationFailedError.for_field(
                "spec.description", f"must be at most {MAX_DESCRIPTION_LENGTH} characters"
            )
        spec["description"] = description.strip()

    model_schema = spec.get("model_schema")
    if model_schema is not None:
        if not isinstance(model_schema, dict):
            raise ValidationFailedError.for_field("spec.model_schema", "must be an object")
        try:
            Draft202012Validator.check_schema(model_schema)
        except SchemaError as exc:
            raise ValidationFailedError.for_field("spec.model_schema", exc.message) from exc
        if model_schema.get("type") not in (None, "object"):
            raise ValidationFailedError.for_field("spec.model_schema.type", "must be 'object'")
        input_schema = spec.get("input_schema")
        if isinstance(input_schema, dict):
            _assert_same_structure(
                model_schema, input_schema, "spec.model_schema", "spec.input_schema"
            )

    returns = spec.get("returns")
    if returns is not None:
        if not isinstance(returns, dict):
            raise ValidationFailedError.for_field("spec.returns", "must be an object")
        unknown = set(returns) - RETURN_KEYS
        if unknown:
            raise ValidationFailedError.for_field(
                "spec.returns", f"unsupported keys {sorted(unknown)}"
            )
        terminal = returns.get("terminal_statuses", [])
        if not isinstance(terminal, list) or any(
            not isinstance(item, str) or not item.strip() for item in terminal
        ):
            raise ValidationFailedError.for_field(
                "spec.returns.terminal_statuses", "must be a list of non-empty strings"
            )


def _assert_same_structure(
    model: dict[str, Any], base: dict[str, Any], model_path: str, base_path: str
) -> None:
    """模型可见契约与调用契约的结构必须等价。

    允许差异只在注解类关键字（description、title、format、examples、default）；
    type、properties 键集合、required、items 与组合分支必须一致，
    否则会出现"模型按 A 构造、平台按 B 校验"的错位。
    """
    if _schema_types(model) != _schema_types(base):
        raise ValidationFailedError.for_field(
            f"{model_path}.type", f"must match {base_path}.type"
        )

    model_props = model.get("properties") or {}
    base_props = base.get("properties") or {}
    if set(model_props) != set(base_props):
        extra = sorted(set(model_props) - set(base_props))
        missing = sorted(set(base_props) - set(model_props))
        problems = []
        if extra:
            problems.append(f"unknown properties {extra}")
        if missing:
            problems.append(f"missing properties {missing}")
        raise ValidationFailedError.for_field(
            f"{model_path}.properties", "; ".join(problems)
        )
    for name, param in base_props.items():
        if isinstance(param, dict) and isinstance(model_props.get(name), dict):
            _assert_same_structure(
                model_props[name],
                param,
                f"{model_path}.properties.{name}",
                f"{base_path}.properties.{name}",
            )

    if set(model.get("required") or []) != set(base.get("required") or []):
        raise ValidationFailedError.for_field(
            f"{model_path}.required", f"must match {base_path}.required"
        )

    model_items = model.get("items")
    base_items = base.get("items")
    if (model_items is None) != (base_items is None):
        raise ValidationFailedError.for_field(
            f"{model_path}.items", f"must match {base_path}.items"
        )
    if isinstance(model_items, dict) and isinstance(base_items, dict):
        _assert_same_structure(
            model_items, base_items, f"{model_path}.items", f"{base_path}.items"
        )

    for key in ("anyOf", "oneOf", "allOf"):
        model_branch = model.get(key)
        base_branch = base.get(key)
        if model_branch is None and base_branch is None:
            continue
        if (
            not isinstance(model_branch, list)
            or not isinstance(base_branch, list)
            or len(model_branch) != len(base_branch)
        ):
            raise ValidationFailedError.for_field(
                f"{model_path}.{key}", f"must match {base_path}.{key}"
            )
        for index, (mine, theirs) in enumerate(zip(model_branch, base_branch, strict=True)):
            if isinstance(mine, dict) and isinstance(theirs, dict):
                _assert_same_structure(
                    mine,
                    theirs,
                    f"{model_path}.{key}[{index}]",
                    f"{base_path}.{key}[{index}]",
                )


def _schema_types(schema: dict[str, Any]) -> tuple[str, ...] | None:
    raw = schema.get("type")
    if raw is None:
        return None
    if isinstance(raw, str):
        return (raw,)
    return tuple(sorted(str(item) for item in raw))


def schema_warnings(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """保存期提示：指出模型可能猜错的参数，不阻塞保存。

    日期类参数没有 format 时，模型只能靠参数名猜格式；
    这类问题不该阻止保存（远端契约可能就是这样），但必须让作者看见。
    """
    schema = spec.get("model_schema") or spec.get("input_schema") or {}
    properties = schema.get("properties") or {}
    warnings: list[dict[str, Any]] = []
    for name, param in properties.items():
        if not isinstance(param, dict) or param.get("format"):
            continue
        if str(param.get("type", "")) not in ("string", "array"):
            continue
        haystack = f"{name} {param.get('title') or ''}".lower()
        if not any(hint in haystack for hint in _DATE_PARAM_HINTS):
            continue
        warnings.append(
            {
                "field": f"spec.input_schema.properties.{name}",
                "reason": (
                    "date-like parameter has no format; "
                    "the model cannot know the expected date format"
                ),
            }
        )
    return warnings
