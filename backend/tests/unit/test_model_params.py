"""模型参数模板与取值校验、Prompt 渲染的纯函数层。"""

from __future__ import annotations

import pytest

from agent_platform.contexts.catalog.domain.params import normalize_params, validate_template
from agent_platform.contexts.catalog.domain.render import render_messages
from agent_platform.shared.errors import ValidationFailedError

TEMPLATE = {
    "temperature": {"type": "number", "min": 0, "max": 2, "default": 0.2},
    "max_tokens": {"type": "integer", "min": 1, "max": 32000, "default": 1024},
    "top_p": {"type": "number", "min": 0, "max": 1},
}


def test_defaults_are_applied() -> None:
    assert normalize_params(TEMPLATE, {}) == {"temperature": 0.2, "max_tokens": 1024}
    assert normalize_params(TEMPLATE, {"top_p": 0.9})["top_p"] == 0.9


def test_out_of_range_reports_field() -> None:
    with pytest.raises(ValidationFailedError) as excinfo:
        normalize_params(TEMPLATE, {"temperature": 3})
    assert excinfo.value.details == [{"field": "params.temperature", "reason": "must be <= 2"}]


def test_unknown_parameter_is_rejected() -> None:
    with pytest.raises(ValidationFailedError) as excinfo:
        normalize_params(TEMPLATE, {"seed": 1})
    assert excinfo.value.details[0]["field"] == "params.seed"


def test_type_mismatch_is_rejected() -> None:
    with pytest.raises(ValidationFailedError):
        normalize_params(TEMPLATE, {"max_tokens": 1.5})
    with pytest.raises(ValidationFailedError):
        normalize_params(TEMPLATE, {"max_tokens": True})
    with pytest.raises(ValidationFailedError):
        normalize_params(TEMPLATE, {"temperature": "hot"})


def test_template_itself_is_validated() -> None:
    with pytest.raises(ValidationFailedError) as excinfo:
        validate_template({"temperature": {"type": "number", "min": 2, "max": 1}})
    assert excinfo.value.details[0]["field"] == "param_template.temperature"

    with pytest.raises(ValidationFailedError):
        validate_template({"temperature": {"type": "float"}})

    with pytest.raises(ValidationFailedError):
        validate_template({"temperature": {"type": "number", "step": 0.1}})

    with pytest.raises(ValidationFailedError) as excinfo:
        validate_template({"temperature": {"type": "number", "min": 0, "max": 2, "default": 9}})
    assert excinfo.value.details[0]["field"] == "params.temperature"


def test_render_substitutes_and_coerces() -> None:
    messages = [
        {"role": "system", "content": "You are {{persona}}."},
        {"role": "user", "content": "limit={{limit}}"},
    ]
    variables = [
        {"name": "persona", "type": "string", "required": True},
        {"name": "limit", "type": "integer", "default": 5},
    ]
    rendered = render_messages(messages, variables, {"persona": "helper"})
    assert rendered[0]["content"] == "You are helper."
    assert rendered[1]["content"] == "limit=5"


def test_render_missing_required_variable_fails_without_partial_output() -> None:
    messages = [{"role": "system", "content": "Hi {{name}}"}]
    with pytest.raises(ValidationFailedError) as excinfo:
        render_messages(messages, [{"name": "name", "type": "string", "required": True}], {})
    assert excinfo.value.details[0]["field"] == "variables.name"


def test_render_type_error_and_unknown_placeholder() -> None:
    with pytest.raises(ValidationFailedError) as excinfo:
        render_messages(
            [{"role": "system", "content": "{{count}}"}],
            [{"name": "count", "type": "integer"}],
            {"count": "many"},
        )
    assert excinfo.value.details[0]["field"] == "variables.count"

    with pytest.raises(ValidationFailedError) as excinfo:
        render_messages([{"role": "system", "content": "{{ghost}}"}], [], {})
    assert excinfo.value.details[0]["field"] == "messages.0.content"
