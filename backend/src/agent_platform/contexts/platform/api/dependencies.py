"""平台 API 的认证依赖。

支持两种调用者身份：
- ``Authorization: Bearer <jwt>``：人类用户；
- ``Authorization: ApiKey <ap_...>`` 或 ``X-API-Key: ap_...``：服务身份。

认证成功后把 ActorContext 写入 contextvars，后续 service 一律从上下文取租户，
不信任请求体里的 tenant_id。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Query, Request

from agent_platform.contexts.platform.application.api_key_service import api_key_service
from agent_platform.contexts.platform.application.auth_service import auth_service
from agent_platform.shared.context import ActorContext, set_actor
from agent_platform.shared.errors import UnauthorizedError

_API_KEY_SCHEMES = {"apikey", "api-key"}


async def get_current_actor(request: Request) -> ActorContext:
    header = request.headers.get("authorization", "").strip()
    api_key_header = request.headers.get("x-api-key", "").strip()

    if header:
        scheme, _, credentials = header.partition(" ")
        credentials = credentials.strip()
        scheme = scheme.lower()
        if not credentials:
            raise UnauthorizedError("malformed authorization header")
        if scheme == "bearer":
            actor = await auth_service.authenticate_token(credentials)
        elif scheme in _API_KEY_SCHEMES:
            actor = await api_key_service.authenticate(credentials)
        else:
            raise UnauthorizedError(f"unsupported authorization scheme: {scheme}")
    elif api_key_header:
        actor = await api_key_service.authenticate(api_key_header)
    else:
        raise UnauthorizedError("authentication required")

    set_actor(actor)
    return actor


CurrentActor = Annotated[ActorContext, Depends(get_current_actor)]


class Pagination:
    def __init__(
        self,
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> None:
        self.limit = limit
        self.offset = offset


CurrentPage = Annotated[Pagination, Depends(Pagination)]
