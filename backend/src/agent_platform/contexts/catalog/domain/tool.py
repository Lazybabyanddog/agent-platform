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

    return {
        **spec,
        "method": method,
        "timeout_s": timeout_s,
        "headers": headers,
        "auth": auth,
    }


def _validate_mcp(spec: dict[str, Any]) -> dict[str, Any]:
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

    tool_name = spec.get("tool_name")
    if not isinstance(tool_name, str) or not tool_name.strip():
        raise ValidationFailedError.for_field("spec.tool_name", "remote tool name is required")
    if len(tool_name) > MAX_TOOL_NAME_LENGTH:
        raise ValidationFailedError.for_field(
            "spec.tool_name", f"must be at most {MAX_TOOL_NAME_LENGTH} characters"
        )

    timeout_s = _validate_timeout(spec)
    headers = _validate_headers(spec)
    auth = _validate_auth(spec)
    _validate_schemas(spec)

    return {
        **spec,
        "transport": transport,
        "tool_name": tool_name,
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
