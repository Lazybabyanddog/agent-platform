"""Outbox 投递失败日志：``event_id`` 等字段不再被 ``extra`` 吞掉。"""

from __future__ import annotations

import json
import uuid
from typing import Any
from unittest.mock import AsyncMock

import pytest

from agent_platform.settings import Settings
from agent_platform.shared.logging import configure_logging, reset_logging
from agent_platform.shared.messaging import publisher as publisher_module
from agent_platform.shared.messaging.publisher import OutboxPublisher


class FailingSink:
    async def publish(self, **_kwargs: Any) -> None:
        raise RuntimeError("broker down")

    async def close(self) -> None:
        return None


@pytest.fixture(autouse=True)
def _clean_logging():
    reset_logging()
    yield
    reset_logging()


def _records(captured: str) -> list[dict]:
    return [json.loads(line) for line in captured.splitlines() if line.startswith("{")]


async def test_publish_failure_logs_event_id(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    configure_logging(Settings(log_level="INFO", log_json=True), service="outbox")
    event = type(
        "Event",
        (),
        {
            "id": uuid.uuid4(),
            "event_type": "agent.release.published",
            "payload": {"agent_id": str(uuid.uuid4())},
        },
    )()
    monkeypatch.setattr(
        publisher_module, "claim_due_batch", AsyncMock(return_value=[event])
    )
    monkeypatch.setattr(publisher_module, "mark_attempt_failed", AsyncMock())

    sent = await OutboxPublisher(FailingSink())._publish_batch(session=object())  # type: ignore[arg-type]

    assert sent == 0
    records = _records(capsys.readouterr().out)
    assert [item["event"] for item in records] == ["outbox.publish_failed"]
    assert records[0]["event_id"] == str(event.id)
    assert records[0]["service"] == "outbox"
    assert "agent_id" not in records[0]  # 载荷不进日志
