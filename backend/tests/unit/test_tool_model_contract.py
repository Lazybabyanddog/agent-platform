"""模型可见契约：用途描述、参数说明、返回语义与保存期告警。

这些字段不影响调用，只决定模型能不能构造出正确参数，
因此单独成组，避免和调用契约的规则混在一起。
"""

from __future__ import annotations

import pytest

from agent_platform.contexts.catalog.domain.enums import ToolType
from agent_platform.contexts.catalog.domain.tool import (
    MAX_DESCRIPTION_LENGTH,
    schema_warnings,
    validate_spec,
)
from agent_platform.shared.errors import ValidationFailedError

HTTP_SPEC: dict = {
    "method": "post",
    "url": "https://tools.local/weather",
    "timeout_s": 5,
    "input_schema": {
        "type": "object",
        "properties": {
            "city": {"type": "string"},
            "start_date": {"type": "string"},
        },
        "required": ["city", "start_date"],
    },
    "output_schema": {"type": "object"},
}


def _field(exc: pytest.ExceptionInfo[ValidationFailedError]) -> str:
    return exc.value.details[0]["field"]


def test_description_is_trimmed_and_kept() -> None:
    normalized = validate_spec({**HTTP_SPEC, "description": "  查询某城市某日天气  "}, ToolType.HTTP)

    assert normalized["description"] == "查询某城市某日天气"


def test_description_must_be_string() -> None:
    with pytest.raises(ValidationFailedError) as excinfo:
        validate_spec({**HTTP_SPEC, "description": 42}, ToolType.HTTP)

    assert _field(excinfo) == "spec.description"


def test_description_length_is_bounded() -> None:
    with pytest.raises(ValidationFailedError) as excinfo:
        validate_spec({**HTTP_SPEC, "description": "x" * (MAX_DESCRIPTION_LENGTH + 1)}, ToolType.HTTP)

    assert _field(excinfo) == "spec.description"


def test_model_schema_may_add_annotations() -> None:
    model_schema = {
        "type": "object",
        "properties": {
            "city": {"type": "string", "description": "中文城市名，如 武汉"},
            "start_date": {
                "type": "string",
                "description": "开始日期",
                "format": "date",
                "examples": ["2026-11-25"],
            },
        },
        "required": ["city", "start_date"],
    }

    normalized = validate_spec({**HTTP_SPEC, "model_schema": model_schema}, ToolType.HTTP)

    assert normalized["model_schema"]["properties"]["start_date"]["format"] == "date"
    # 调用契约不被模型可见契约改写
    assert "format" not in normalized["input_schema"]["properties"]["start_date"]


def test_model_schema_type_mismatch_is_rejected() -> None:
    model_schema = {
        "type": "object",
        "properties": {
            "city": {"type": "integer"},
            "start_date": {"type": "string"},
        },
        "required": ["city", "start_date"],
    }

    with pytest.raises(ValidationFailedError) as excinfo:
        validate_spec({**HTTP_SPEC, "model_schema": model_schema}, ToolType.HTTP)

    assert _field(excinfo) == "spec.model_schema.properties.city.type"


def test_model_schema_cannot_drop_a_parameter() -> None:
    model_schema = {
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city", "start_date"],
    }

    with pytest.raises(ValidationFailedError) as excinfo:
        validate_spec({**HTTP_SPEC, "model_schema": model_schema}, ToolType.HTTP)

    assert _field(excinfo) == "spec.model_schema.properties"


def test_model_schema_cannot_add_a_parameter() -> None:
    model_schema = {
        "type": "object",
        "properties": {
            "city": {"type": "string"},
            "start_date": {"type": "string"},
            "end_date": {"type": "string"},
        },
        "required": ["city", "start_date"],
    }

    with pytest.raises(ValidationFailedError) as excinfo:
        validate_spec({**HTTP_SPEC, "model_schema": model_schema}, ToolType.HTTP)

    assert _field(excinfo) == "spec.model_schema.properties"


def test_model_schema_cannot_change_required() -> None:
    model_schema = {
        "type": "object",
        "properties": {
            "city": {"type": "string"},
            "start_date": {"type": "string"},
        },
        "required": ["city"],
    }

    with pytest.raises(ValidationFailedError) as excinfo:
        validate_spec({**HTTP_SPEC, "model_schema": model_schema}, ToolType.HTTP)

    assert _field(excinfo) == "spec.model_schema.required"


def test_model_schema_must_be_object_schema() -> None:
    with pytest.raises(ValidationFailedError) as excinfo:
        validate_spec({**HTTP_SPEC, "model_schema": {"type": "array"}}, ToolType.HTTP)

    assert _field(excinfo) == "spec.model_schema.type"


def test_returns_accepts_terminal_statuses() -> None:
    normalized = validate_spec(
        {**HTTP_SPEC, "returns": {"terminal_statuses": ["no_data"]}}, ToolType.HTTP
    )

    assert normalized["returns"] == {"terminal_statuses": ["no_data"]}


def test_returns_rejects_unknown_keys() -> None:
    with pytest.raises(ValidationFailedError) as excinfo:
        validate_spec({**HTTP_SPEC, "returns": {"retryable": True}}, ToolType.HTTP)

    assert _field(excinfo) == "spec.returns"


def test_returns_rejects_non_string_status() -> None:
    with pytest.raises(ValidationFailedError) as excinfo:
        validate_spec({**HTTP_SPEC, "returns": {"terminal_statuses": [1]}}, ToolType.HTTP)

    assert _field(excinfo) == "spec.returns.terminal_statuses"


def test_schema_warnings_flag_date_without_format() -> None:
    warnings = schema_warnings(HTTP_SPEC)

    fields = {item["field"] for item in warnings}
    assert fields == {"spec.input_schema.properties.start_date"}


def test_schema_warnings_silent_when_format_present() -> None:
    spec = {
        **HTTP_SPEC,
        "input_schema": {
            "type": "object",
            "properties": {
                "city": {"type": "string"},
                "start_date": {"type": "string", "format": "date"},
            },
            "required": ["city", "start_date"],
        },
    }

    assert schema_warnings(spec) == []


def test_schema_warnings_prefer_model_schema() -> None:
    spec = {
        **HTTP_SPEC,
        "model_schema": {
            "type": "object",
            "properties": {
                "city": {"type": "string"},
                "start_date": {"type": "string", "format": "date"},
            },
            "required": ["city", "start_date"],
        },
    }

    assert schema_warnings(spec) == []


def test_schema_warnings_ignore_non_date_fields() -> None:
    spec = {
        **HTTP_SPEC,
        "input_schema": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    }

    assert schema_warnings(spec) == []
