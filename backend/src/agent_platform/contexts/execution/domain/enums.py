"""execution 上下文的枚举与失败分类。"""

from __future__ import annotations

from enum import StrEnum


class RunStatus(StrEnum):
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class RunKind(StrEnum):
    """运行种类：正式调用与预览执行分开统计、分开解释。"""

    INVOKE = "INVOKE"
    PREVIEW = "PREVIEW"


class SessionStatus(StrEnum):
    IDLE = "IDLE"
    BUSY = "BUSY"


class CallerType(StrEnum):
    USER = "USER"
    API_KEY = "API_KEY"


class RunErrorCode(StrEnum):
    INPUT_INVALID = "INPUT_INVALID"
    RESOURCE_UNAVAILABLE = "RESOURCE_UNAVAILABLE"
    MODEL_ERROR = "MODEL_ERROR"
    TOOL_ERROR = "TOOL_ERROR"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    TIMEOUT = "TIMEOUT"
    INTERNAL = "INTERNAL"


class ModelErrorCode(StrEnum):
    RATE_LIMITED = "RATE_LIMITED"
    TIMEOUT = "TIMEOUT"
    AUTH = "AUTH"
    INVALID_REQUEST = "INVALID_REQUEST"
    UPSTREAM = "UPSTREAM"
    UNKNOWN = "UNKNOWN"
