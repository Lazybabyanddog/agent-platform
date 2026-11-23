"""工具契约校验：HTTP 与 MCP 两套规则，按类型分派。"""

from __future__ import annotations

import pytest

from agent_platform.contexts.catalog.domain.enums import ToolType
from agent_platform.contexts.catalog.domain.tool import MAX_TIMEOUT_S, validate_spec
from agent_platform.shared.errors import ValidationFailedError

MCP_SPEC = {
    "transport": "streamable_http",
    "url": "https://knowledge.example.com/mcp",
    "tool_name": "search_documents",
    "timeout_s": 15,
    "auth": {"type": "none"},
    "input_schema": {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    },
    "output_schema": {"type": "object"},
}


def _field(exc: pytest.ExceptionInfo[ValidationFailedError]) -> str:
    return exc.value.details[0]["field"]


def test_mcp_spec_is_normalized() -> None:
    normalized = validate_spec(MCP_SPEC, ToolType.MCP)

    assert normalized["transport"] == "streamable_http"
    assert normalized["tool_name"] == "search_documents"
    assert normalized["timeout_s"] == 15.0
    assert normalized["auth"] == {"type": "none"}


def test_mcp_transport_defaults_to_streamable_http() -> None:
    spec = {key: value for key, value in MCP_SPEC.items() if key != "transport"}
    normalized = validate_spec(spec, ToolType.MCP)

    assert normalized["transport"] == "streamable_http"


def test_mcp_sse_transport_is_allowed() -> None:
    normalized = validate_spec({**MCP_SPEC, "transport": "sse"}, ToolType.MCP)

    assert normalized["transport"] == "sse"


def test_mcp_schemas_are_optional() -> None:
    spec = {key: value for key, value in MCP_SPEC.items() if key not in ("input_schema", "output_schema")}

    assert validate_spec(spec, ToolType.MCP)["tool_name"] == "search_documents"


def test_mcp_missing_tool_name_is_rejected() -> None:
    spec = {key: value for key, value in MCP_SPEC.items() if key != "tool_name"}

    with pytest.raises(ValidationFailedError) as excinfo:
        validate_spec(spec, ToolType.MCP)
    assert _field(excinfo) == "spec.tool_name"


def test_mcp_blank_tool_name_is_rejected() -> None:
    with pytest.raises(ValidationFailedError) as excinfo:
        validate_spec({**MCP_SPEC, "tool_name": "   "}, ToolType.MCP)
    assert _field(excinfo) == "spec.tool_name"


def test_mcp_tool_name_length_is_bounded() -> None:
    with pytest.raises(ValidationFailedError) as excinfo:
        validate_spec({**MCP_SPEC, "tool_name": "n" * 129}, ToolType.MCP)
    assert _field(excinfo) == "spec.tool_name"


def test_mcp_stdio_transport_is_rejected() -> None:
    with pytest.raises(ValidationFailedError) as excinfo:
        validate_spec({**MCP_SPEC, "transport": "stdio"}, ToolType.MCP)
    assert _field(excinfo) == "spec.transport"


@pytest.mark.parametrize("url", ["knowledge.example.com/mcp", "ftp://knowledge.example.com", ""])
def test_mcp_relative_or_non_http_url_is_rejected(url: str) -> None:
    with pytest.raises(ValidationFailedError) as excinfo:
        validate_spec({**MCP_SPEC, "url": url}, ToolType.MCP)
    assert _field(excinfo) == "spec.url"


@pytest.mark.parametrize("timeout_s", [0, MAX_TIMEOUT_S + 1, "15", True])
def test_mcp_timeout_out_of_range_is_rejected(timeout_s: object) -> None:
    with pytest.raises(ValidationFailedError) as excinfo:
        validate_spec({**MCP_SPEC, "timeout_s": timeout_s}, ToolType.MCP)
    assert _field(excinfo) == "spec.timeout_s"


def test_mcp_auth_requires_secret_ref() -> None:
    with pytest.raises(ValidationFailedError) as excinfo:
        validate_spec({**MCP_SPEC, "auth": {"type": "bearer"}}, ToolType.MCP)
    assert _field(excinfo) == "spec.auth.secret_ref_id"


def test_mcp_unknown_fields_are_ignored() -> None:
    normalized = validate_spec({**MCP_SPEC, "unexpected": "value"}, ToolType.MCP)

    assert normalized["tool_name"] == "search_documents"


def test_http_spec_still_validates_method_and_headers() -> None:
    normalized = validate_spec(
        {"method": "post", "url": "https://tools.local/echo", "timeout_s": 5}, ToolType.HTTP
    )

    assert normalized["method"] == "POST"
    assert normalized["timeout_s"] == 5.0
    assert normalized["headers"] == {}

    with pytest.raises(ValidationFailedError) as excinfo:
        validate_spec({"method": "TRACE", "url": "https://tools.local/echo"}, ToolType.HTTP)
    assert _field(excinfo) == "spec.method"


def test_http_spec_requires_url() -> None:
    with pytest.raises(ValidationFailedError) as excinfo:
        validate_spec({"method": "POST"}, ToolType.HTTP)
    assert _field(excinfo) == "spec.url"


def test_spec_must_be_object() -> None:
    with pytest.raises(ValidationFailedError) as excinfo:
        validate_spec("not-a-dict", ToolType.MCP)  # type: ignore[arg-type]
    assert _field(excinfo) == "spec"
