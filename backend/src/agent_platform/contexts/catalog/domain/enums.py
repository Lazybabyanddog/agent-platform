from __future__ import annotations

from enum import StrEnum


class ProviderStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class ModelStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class ProbeVerdict(StrEnum):
    """连通性探测的六种结论；序列化即稳定字符串，前端不做二次推断。"""

    OK = "OK"
    MODEL_NOT_FOUND = "MODEL_NOT_FOUND"
    CREDENTIAL_OK_MODEL_UNVERIFIED = "CREDENTIAL_OK_MODEL_UNVERIFIED"
    MODEL_PROBE_AUTH_FAILED = "MODEL_PROBE_AUTH_FAILED"
    MODEL_PROBE_TIMEOUT = "MODEL_PROBE_TIMEOUT"
    MODEL_PROBE_UNREACHABLE = "MODEL_PROBE_UNREACHABLE"


class PromptStatus(StrEnum):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class ToolStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class ToolType(StrEnum):
    HTTP = "HTTP"
    MCP = "MCP"


class RiskLevel(StrEnum):
    READ_ONLY = "READ_ONLY"
    WRITE = "WRITE"
    HIGH = "HIGH"


class McpProbeVerdict(StrEnum):
    """MCP 工具探测的结论。

    刻意不复用 `ProbeVerdict`：那套取值全是模型语义（`MODEL_NOT_FOUND` 等），
    混用会让前端在 MCP 场景下拿到无法解释的结论。
    """

    OK = "OK"
    MCP_PROBE_AUTH_FAILED = "MCP_PROBE_AUTH_FAILED"
    MCP_PROBE_TIMEOUT = "MCP_PROBE_TIMEOUT"
    MCP_PROBE_UNREACHABLE = "MCP_PROBE_UNREACHABLE"
    MCP_PROBE_PROTOCOL_ERROR = "MCP_PROBE_PROTOCOL_ERROR"
