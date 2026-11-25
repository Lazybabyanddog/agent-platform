"""日志管道：字段契约、级别重配、文件落地与轮转、落盘降级、uvicorn 接入。"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from agent_platform.settings import Settings
from agent_platform.shared.logging import (
    CONTRACT_FIELDS,
    configure_logging,
    get_logger,
    reset_logging,
)


@pytest.fixture(autouse=True)
def _clean_logging():
    reset_logging()
    yield
    reset_logging()


def _records(captured: str) -> list[dict]:
    return [json.loads(line) for line in captured.splitlines() if line.startswith("{")]


def test_event_carries_contract_fields(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(Settings(log_level="INFO", log_json=True), service="api")
    get_logger("pipeline").info("llm.call", model_name="gpt-4o-mini")

    records = _records(capsys.readouterr().out)

    assert len(records) == 1
    record = records[0]
    assert set(CONTRACT_FIELDS) <= set(record)
    assert record["event"] == "llm.call"
    assert record["service"] == "api"
    assert record["environment"] == "dev"
    assert record["trace_id"] is None
    assert record["tenant_id"] is None
    assert record["run_id"] is None


def test_reset_logging_applies_new_level(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(Settings(log_level="INFO", log_json=True), service="api")
    get_logger("pipeline").debug("runtime.node", node="llm")
    assert _records(capsys.readouterr().out) == []

    reset_logging()
    configure_logging(Settings(log_level="DEBUG", log_json=True), service="api")
    get_logger("pipeline").debug("runtime.node", node="llm")

    records = _records(capsys.readouterr().out)
    assert [item["event"] for item in records] == ["runtime.node"]


def test_service_name_is_process_level(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(Settings(log_level="INFO", log_json=True), service="outbox")
    get_logger("pipeline").info("outbox.started", exchange="agent_platform.events")

    record = _records(capsys.readouterr().out)[0]
    assert record["service"] == "outbox"
    assert record["exchange"] == "agent_platform.events"


def test_file_handler_rotates(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    settings = Settings(
        log_level="INFO",
        log_json=True,
        log_dir=str(tmp_path),
        log_max_bytes=200,
        log_backup_count=1,
    )
    configure_logging(settings, service="api")
    logger = get_logger("pipeline")
    for index in range(20):
        logger.info("runtime.node", node=f"n{index}", used_steps=index)

    reset_logging()  # 先释放句柄再读文件，避免 Windows 上的占用

    current = tmp_path / "api.log"
    assert current.exists()
    assert (tmp_path / "api.log.1").exists()
    lines = [line for line in current.read_text(encoding="utf-8").splitlines() if line]
    assert lines
    for line in lines:
        assert json.loads(line)["event"] == "runtime.node"


def test_unusable_log_dir_degrades_to_stdout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    blocker = tmp_path / "logs"
    blocker.write_text("not a directory", encoding="utf-8")

    configure_logging(
        Settings(log_level="INFO", log_json=True, log_dir=str(blocker)), service="api"
    )
    get_logger("pipeline").info("llm.call", model_name="gpt-4o-mini")

    events = [item["event"] for item in _records(capsys.readouterr().out)]
    assert "logging.degraded" in events
    assert "llm.call" in events


def test_uvicorn_access_joins_pipeline(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(Settings(log_level="INFO", log_json=True), service="api")
    logging.getLogger("uvicorn.access").info(
        '%s - "%s %s" %d', "127.0.0.1:5000", "GET", "/healthz", 200
    )

    record = _records(capsys.readouterr().out)[0]

    assert record["event"] == "uvicorn.access"
    assert record["message"].startswith("127.0.0.1:5000")
    assert set(CONTRACT_FIELDS) <= set(record)
