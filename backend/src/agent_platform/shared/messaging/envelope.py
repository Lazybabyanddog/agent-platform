"""领域事件信封。

所有对外事件共用同一个外壳，消费方无需为每种事件单独约定元数据。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

EVENT_SCHEMA_VERSION = 1
EXCHANGE_NAME = "agent_platform.events"


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class EventEnvelope:
    event_id: uuid.UUID
    event_type: str
    tenant_id: uuid.UUID
    occurred_at: datetime
    payload: dict[str, Any]
    schema_version: int = EVENT_SCHEMA_VERSION
    trace_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": str(self.event_id),
            "event_type": self.event_type,
            "event_version": self.schema_version,
            "tenant_id": str(self.tenant_id),
            "occurred_at": self.occurred_at.isoformat(),
            "trace_id": self.trace_id,
            "payload": self.payload,
            **self.extra,
        }
