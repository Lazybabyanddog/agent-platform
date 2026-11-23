from __future__ import annotations

import time
import uuid

from agent_platform.shared.ids import new_id, new_id_str, timestamp_ms


def test_uuid7_shape():
    value = new_id()

    assert isinstance(value, uuid.UUID)
    assert value.version == 7
    assert value.variant == uuid.RFC_4122


def test_uuid7_is_time_ordered():
    ids = [new_id() for _ in range(2000)]

    assert ids == sorted(ids)
    assert len(set(ids)) == len(ids)


def test_uuid7_embeds_current_time():
    before = int(time.time() * 1000)
    value = new_id()
    after = int(time.time() * 1000)

    assert before - 5 <= timestamp_ms(value) <= after + 5


def test_uuid7_str():
    text = new_id_str()
    assert isinstance(text, str)
    assert uuid.UUID(text).version == 7


def test_sequence_overflow_bumps_timestamp():
    """同一毫秒内超过 4096 个 id 时时间戳前移，仍然单调。"""
    ids = [new_id() for _ in range(5000)]
    assert ids == sorted(ids)
    assert len(set(ids)) == 5000
