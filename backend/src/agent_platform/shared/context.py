"""请求级上下文：trace_id / request_id / 租户 / 调用者。

全部用 contextvars 承载，禁止通过函数参数层层传递身份信息。
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ActorContext:
    """已认证的调用者。tenant_id 只来自认证结果，永不来自请求体。"""

    tenant_id: uuid.UUID
    actor_type: str  # USER | API_KEY | SYSTEM
    actor_id: uuid.UUID
    permissions: frozenset[str] = frozenset()
    roles: frozenset[str] = frozenset()
    display_name: str | None = None

    def has_permission(self, permission: str) -> bool:
        return permission in self.permissions

    @property
    def is_admin(self) -> bool:
        return bool(self.roles & {"platform_admin", "tenant_admin"})


_trace_id: ContextVar[str | None] = ContextVar("trace_id", default=None)
_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_actor: ContextVar[ActorContext | None] = ContextVar("actor", default=None)


def set_trace_id(value: str) -> None:
    _trace_id.set(value)


def get_trace_id() -> str | None:
    return _trace_id.get()


def set_request_id(value: str) -> None:
    _request_id.set(value)


def get_request_id() -> str | None:
    return _request_id.get()


def set_actor(actor: ActorContext | None) -> None:
    _actor.set(actor)


def get_actor() -> ActorContext | None:
    return _actor.get()


def require_actor() -> ActorContext:
    actor = _actor.get()
    if actor is None:
        from agent_platform.shared.errors import UnauthorizedError

        raise UnauthorizedError("authentication required")
    return actor


def current_tenant_id() -> uuid.UUID | None:
    actor = _actor.get()
    return actor.tenant_id if actor else None
