"""insight 上下文的枚举。"""

from __future__ import annotations

from enum import StrEnum


class CallStatus(StrEnum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
