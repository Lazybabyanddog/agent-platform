"""MCP 返回内容归一化：文本、JSON、混合内容的收敛规则。"""

from __future__ import annotations

from types import SimpleNamespace

from agent_platform.contexts.catalog.infrastructure.mcp_client import (
    McpCallResult,
    normalize_content,
    summarize_blocks,
)


def _text(value: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=value)


def _image() -> SimpleNamespace:
    return SimpleNamespace(
        type="image",
        model_dump=lambda exclude_none=True: {
            "type": "image",
            "mimeType": "image/png",
            "data": "aGVsbG8=",
        },
    )


def test_single_text_json_is_parsed() -> None:
    assert normalize_content([_text('{"documents": [1, 2]}')]) == {"documents": [1, 2]}


def test_single_text_null_json_is_parsed() -> None:
    assert normalize_content([_text("null")]) is None


def test_single_plain_text_stays_text() -> None:
    assert normalize_content([_text("hello")]) == "hello"


def test_multiple_text_blocks_are_joined() -> None:
    assert normalize_content([_text("a"), _text("b")]) == "a\nb"


def test_empty_content_becomes_empty_string() -> None:
    assert normalize_content([]) == ""


def test_non_text_content_becomes_structured_summary() -> None:
    output = normalize_content([_image()])

    assert output["content"][0]["type"] == "image"
    assert "image/png" in output["content"][0]["summary"]


def test_mixed_content_uses_structured_summary() -> None:
    output = normalize_content([_text("see attached"), _image()])

    assert [item["type"] for item in output["content"]] == ["text", "image"]
    assert output["content"][0]["summary"] == "see attached"


def test_summary_is_truncated() -> None:
    long_text = "x" * 2000

    summary = summarize_blocks([_text(long_text)])

    assert len(summary) <= 503
    assert summary.endswith("...")


def test_summary_marks_non_text_blocks() -> None:
    assert summarize_blocks([_image()]) == "<image>"


def test_call_result_carries_remote_tool_name() -> None:
    result = McpCallResult(output={"ok": True}, remote_tool_name="search_documents")

    assert result.remote_tool_name == "search_documents"
    assert result.is_error is False
    assert result.error_summary is None
