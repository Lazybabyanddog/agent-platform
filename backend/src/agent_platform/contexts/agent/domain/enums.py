from __future__ import annotations

from enum import StrEnum


class AgentStatus(StrEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    ARCHIVED = "ARCHIVED"


class AgentType(StrEnum):
    CHAT = "CHAT"


class Environment(StrEnum):
    DEV = "DEV"
    TEST = "TEST"
    PROD = "PROD"


class ReleaseStatus(StrEnum):
    RELEASED = "RELEASED"
    ROLLED_BACK = "ROLLED_BACK"


class BindingRole(StrEnum):
    MODEL = "model"
    PROMPT = "prompt"
    TOOL = "tool"


class BindingResourceType(StrEnum):
    MODEL = "model"
    PROMPT_VERSION = "prompt_version"
    TOOL_VERSION = "tool_version"
