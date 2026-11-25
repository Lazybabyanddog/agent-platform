"""工具注册与执行器。

执行器负责四件事：入参校验、鉴权注入、超时控制、出参结构校验，HTTP 与 MCP 共用这套骨架。
任何一步不符合契约都返回可识别的错误码，不把上游原始响应直接透给调用方。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter
from typing import Any

import httpx
from jsonschema import ValidationError as JsonSchemaValidationError
from jsonschema import validate as jsonschema_validate
from sqlalchemy.exc import IntegrityError

from agent_platform.contexts.catalog.domain.enums import RiskLevel, ToolStatus, ToolType
from agent_platform.contexts.catalog.domain.errors import (
    HighRiskToolError,
    ToolExecutionError,
    ToolMcpError,
    ToolOutputInvalidError,
    ToolTimeoutError,
)
from agent_platform.contexts.catalog.domain.tool import schema_warnings, validate_spec
from agent_platform.contexts.catalog.infrastructure.mcp_client import McpSession, SdkMcpSession
from agent_platform.contexts.catalog.infrastructure.repositories import (
    ToolGrantRepository,
    ToolRepository,
    ToolVersionRepository,
)
from agent_platform.contexts.catalog.infrastructure.tables import Tool, ToolVersion
from agent_platform.contexts.platform.application.secret_service import secret_service
from agent_platform.shared.context import ActorContext
from agent_platform.shared.db import session_scope
from agent_platform.shared.errors import (
    ConflictError,
    NotFoundError,
    ResourceUnavailableError,
    ValidationFailedError,
)
from agent_platform.shared.hashing import config_hash


@dataclass(frozen=True, slots=True)
class ToolView:
    """工具列表/详情的聚合视图：基础标识 + 最新版本风险与版本号 + 本租户授权状态。"""

    id: uuid.UUID
    code: str
    name: str
    description: str | None
    type: ToolType
    status: ToolStatus
    risk_level: RiskLevel | None
    latest_version_no: int | None
    granted: bool
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ToolVersionSaveResult:
    """保存版本的结果：版本本身 + 不阻塞保存的契约提示。"""

    version: ToolVersion
    warnings: list[dict[str, Any]]


@dataclass(frozen=True, slots=True)
class ToolGrantView:
    granted: bool
    granted_at: datetime | None
    granted_by: uuid.UUID | None


def _with_model_description(
    spec: dict[str, Any], tool_description: str | None
) -> dict[str, Any]:
    """把面向模型的用途描述固化进版本契约。

    描述属于语义，语义随参数变化，因此以版本为准；
    版本未写时继承工具级描述，两者都空则拒绝保存——
    模型拿不到用途描述时只会滥用工具。
    """
    description = (spec.get("description") or "").strip()
    if not description:
        description = (tool_description or "").strip()
    if not description:
        raise ValidationFailedError.for_field(
            "spec.description",
            "a model-facing description is required on the version or the tool",
        )
    return {**spec, "description": description}


def _to_view(tool: Tool, version: ToolVersion | None, *, granted: bool) -> ToolView:
    return ToolView(
        id=tool.id,
        code=tool.code,
        name=tool.name,
        description=tool.description,
        type=tool.type,
        status=tool.status,
        risk_level=version.risk_level if version is not None else None,
        latest_version_no=version.version_no if version is not None else None,
        granted=granted,
        updated_at=tool.updated_at,
    )


@dataclass(frozen=True, slots=True)
class ToolCallResult:
    status: str
    output: Any
    latency_ms: int
    request_digest: str
    response_digest: str | None
    error_code: str | None = None


async def build_auth_headers(*, tenant_id: uuid.UUID, spec: dict[str, Any]) -> dict[str, str]:
    """静态请求头 + 按 auth 声明解析密钥。

    执行与探测共用同一条路径：否则很容易出现「探测按 Bearer 拼、调用按 header 拼」
    这类错位，表现为探测 401、真正调用正常（或反过来）。
    """
    headers = dict(spec.get("headers") or {})
    auth = spec.get("auth") or {"type": "none"}
    if auth.get("type") == "bearer":
        secret = await secret_service.resolve(
            tenant_id=tenant_id, secret_id=uuid.UUID(str(auth["secret_ref_id"]))
        )
        headers["Authorization"] = f"Bearer {secret}"
    elif auth.get("type") == "header":
        secret = await secret_service.resolve(
            tenant_id=tenant_id, secret_id=uuid.UUID(str(auth["secret_ref_id"]))
        )
        headers[str(auth["name"])] = secret
    return headers


class ToolService:
    def __init__(
        self,
        transport: httpx.AsyncBaseTransport | None = None,
        *,
        mcp_session: McpSession | None = None,
    ) -> None:
        # 测试可注入 transport / mcp session，生产环境走默认网络栈
        self._transport = transport
        self._mcp_session: McpSession = mcp_session or SdkMcpSession()

    async def create(
        self,
        *,
        actor: ActorContext,
        code: str,
        name: str,
        description: str | None,
        spec: dict[str, Any],
        risk_level: RiskLevel,
        type: ToolType = ToolType.HTTP,
    ) -> tuple[ToolView, ToolVersion]:
        normalized = validate_spec(spec, type)
        async with session_scope(actor.tenant_id) as session:
            repo = ToolRepository()
            if await repo.by_code(session, tenant_id=actor.tenant_id, code=code):
                raise ConflictError(f"tool code {code!r} already exists")
            tool = await repo.create(
                session,
                tenant_id=actor.tenant_id,
                code=code,
                name=name,
                description=description,
                type=type,
            )
            version = await ToolVersionRepository().create(
                session,
                tenant_id=actor.tenant_id,
                tool_id=tool.id,
                version_no=1,
                spec=normalized,
                risk_level=risk_level,
                config_hash=config_hash({"spec": normalized, "risk_level": risk_level.value}),
            )
            return _to_view(tool, version, granted=False), version

    async def list(self, *, actor: ActorContext) -> list[ToolView]:
        async with session_scope(actor.tenant_id) as session:
            tools = await ToolRepository().list(session, tenant_id=actor.tenant_id)
            versions = await ToolVersionRepository().latest_for_tools(
                session, tool_ids=[tool.id for tool in tools]
            )
            grants = await ToolGrantRepository().list(session, tenant_id=actor.tenant_id)
            granted_tool_ids = {grant.tool_id for grant in grants}
            return [
                _to_view(tool, versions.get(tool.id), granted=tool.id in granted_tool_ids)
                for tool in tools
            ]

    async def get(self, *, actor: ActorContext, tool_id: uuid.UUID) -> ToolView:
        async with session_scope(actor.tenant_id) as session:
            tool = await ToolRepository().by_id(
                session, tenant_id=actor.tenant_id, tool_id=tool_id
            )
            if tool is None:
                raise NotFoundError("tool not found")
            versions = await ToolVersionRepository().latest_for_tools(session, tool_ids=[tool.id])
            granted = (
                await ToolGrantRepository().get(
                    session, tenant_id=actor.tenant_id, tool_id=tool.id
                )
                is not None
            )
            return _to_view(tool, versions.get(tool.id), granted=granted)

    async def set_status(
        self, *, actor: ActorContext, tool_id: uuid.UUID, status: ToolStatus
    ) -> ToolView:
        """幂等切换启用状态：目标状态与当前一致时直接返回视图，不产生写操作。"""
        async with session_scope(actor.tenant_id) as session:
            tool = await ToolRepository().by_id(
                session, tenant_id=actor.tenant_id, tool_id=tool_id
            )
            if tool is None:
                raise NotFoundError("tool not found")
            if tool.status != status:
                tool.status = status
                tool.updated_at = datetime.now(UTC)
                await session.flush()
            version = await ToolVersionRepository().latest(session, tool_id=tool.id)
            granted = (
                await ToolGrantRepository().get(
                    session, tenant_id=actor.tenant_id, tool_id=tool.id
                )
                is not None
            )
            return _to_view(tool, version, granted=granted)

    async def get_grant(self, *, actor: ActorContext, tool_id: uuid.UUID) -> ToolGrantView:
        async with session_scope(actor.tenant_id) as session:
            if await ToolRepository().by_id(
                session, tenant_id=actor.tenant_id, tool_id=tool_id
            ) is None:
                raise NotFoundError("tool not found")
            grant = await ToolGrantRepository().get(
                session, tenant_id=actor.tenant_id, tool_id=tool_id
            )
            if grant is None:
                return ToolGrantView(granted=False, granted_at=None, granted_by=None)
            return ToolGrantView(
                granted=True, granted_at=grant.created_at, granted_by=grant.granted_by
            )

    async def list_versions(self, *, actor: ActorContext, tool_id: uuid.UUID) -> list[ToolVersion]:
        async with session_scope(actor.tenant_id) as session:
            if await ToolRepository().by_id(
                session, tenant_id=actor.tenant_id, tool_id=tool_id
            ) is None:
                raise NotFoundError("tool not found")
            return await ToolVersionRepository().list_for_tool(session, tool_id=tool_id)

    async def add_version(
        self,
        *,
        actor: ActorContext,
        tool_id: uuid.UUID,
        spec: dict[str, Any],
        risk_level: RiskLevel,
    ) -> ToolVersionSaveResult:
        async with session_scope(actor.tenant_id) as session:
            tool = await ToolRepository().by_id(
                session, tenant_id=actor.tenant_id, tool_id=tool_id
            )
            if tool is None:
                raise NotFoundError("tool not found")
            # 版本必须与工具既有接入类型保持一致，避免同一工具出现两套契约
            normalized = validate_spec(spec, tool.type)
            normalized = _with_model_description(normalized, tool.description)
            warnings = schema_warnings(normalized)
            repo = ToolVersionRepository()
            version_no = await repo.next_version_no(session, tool_id=tool_id)
            try:
                async with session.begin_nested():
                    version = await repo.create(
                        session,
                        tenant_id=actor.tenant_id,
                        tool_id=tool_id,
                        version_no=version_no,
                        spec=normalized,
                        risk_level=risk_level,
                        config_hash=config_hash(
                            {"spec": normalized, "risk_level": risk_level.value}
                        ),
                    )
            except IntegrityError as exc:
                raise ConflictError("tool version conflict, retry the request") from exc
            return ToolVersionSaveResult(version=version, warnings=warnings)

    # ---------------------------------------------------------- 授权

    async def grant(self, *, actor: ActorContext, tool_id: uuid.UUID) -> None:
        async with session_scope(actor.tenant_id) as session:
            if await ToolRepository().by_id(
                session, tenant_id=actor.tenant_id, tool_id=tool_id
            ) is None:
                raise NotFoundError("tool not found")
            repo = ToolGrantRepository()
            if await repo.get(session, tenant_id=actor.tenant_id, tool_id=tool_id) is not None:
                raise ConflictError("tool is already granted to this tenant")
            await repo.create(
                session, tenant_id=actor.tenant_id, tool_id=tool_id, granted_by=actor.actor_id
            )

    async def revoke(self, *, actor: ActorContext, tool_id: uuid.UUID) -> None:
        async with session_scope(actor.tenant_id) as session:
            deleted = await ToolGrantRepository().delete(
                session, tenant_id=actor.tenant_id, tool_id=tool_id
            )
            if deleted == 0:
                raise NotFoundError("tool grant not found")

    async def check_invocable(self, *, tenant_id: uuid.UUID, tool_version_id: uuid.UUID) -> None:
        """Agent 发布/执行前的前置校验：工具存在、启用、且本租户已获授权。"""
        async with session_scope(tenant_id) as session:
            version = await ToolVersionRepository().by_id(
                session, tenant_id=tenant_id, version_id=tool_version_id
            )
            if version is None:
                raise NotFoundError("tool version not found")
            tool = await ToolRepository().by_id(
                session, tenant_id=tenant_id, tool_id=version.tool_id
            )
            if tool is None or tool.status != ToolStatus.ACTIVE:
                raise ResourceUnavailableError("tool is not available")
            if await ToolGrantRepository().get(
                session, tenant_id=tenant_id, tool_id=tool.id
            ) is None:
                raise ResourceUnavailableError("tool is not granted to this tenant")

    # ---------------------------------------------------------- 执行

    async def execute(
        self,
        *,
        actor: ActorContext,
        tool_version_id: uuid.UUID,
        arguments: dict[str, Any],
        allow_high_risk: bool = False,
    ) -> ToolCallResult:
        await self.check_invocable(tenant_id=actor.tenant_id, tool_version_id=tool_version_id)
        async with session_scope(actor.tenant_id) as session:
            version = await ToolVersionRepository().by_id(
                session, tenant_id=actor.tenant_id, version_id=tool_version_id
            )
            if version is None:
                raise NotFoundError("tool version not found")
            tool = await ToolRepository().by_id(
                session, tenant_id=actor.tenant_id, tool_id=version.tool_id
            )
            spec, risk_level = version.spec, version.risk_level
            tool_type = tool.type if tool is not None else ToolType.HTTP

        if risk_level == RiskLevel.HIGH and not allow_high_risk:
            raise HighRiskToolError(
                details=[{"field": "risk_level", "reason": "HIGH risk requires approval"}]
            )

        _validate_arguments(arguments, spec)
        headers = await build_auth_headers(tenant_id=actor.tenant_id, spec=spec)

        if tool_type == ToolType.MCP:
            return await self._execute_mcp(
                actor=actor, spec=spec, arguments=arguments, headers=headers
            )
        return await self._execute_http(spec=spec, arguments=arguments, headers=headers)

    async def _execute_http(
        self,
        *,
        spec: dict[str, Any],
        arguments: dict[str, Any],
        headers: dict[str, str],
    ) -> ToolCallResult:
        method = spec["method"]
        url = spec["url"]
        timeout_s = float(spec.get("timeout_s", 15))
        request_digest = config_hash({"method": method, "url": url, "arguments": arguments})

        started = perf_counter()
        try:
            async with httpx.AsyncClient(timeout=timeout_s, transport=self._transport) as client:
                response = await client.request(
                    method,
                    url,
                    headers=headers,
                    params=arguments if method == "GET" else None,
                    json=None if method == "GET" else arguments,
                )
        except httpx.TimeoutException as exc:
            raise ToolTimeoutError(
                f"tool call exceeded {timeout_s}s",
                details=[{"field": "timeout_s", "reason": "upstream did not respond in time"}],
            ) from exc
        except httpx.HTTPError as exc:
            raise ToolExecutionError(f"tool call failed: {type(exc).__name__}") from exc

        latency_ms = max(int((perf_counter() - started) * 1000), 0)
        body = _parse_body(response)
        if response.status_code >= 400:
            raise ToolExecutionError(
                f"tool returned HTTP {response.status_code}",
                details=[
                    {
                        "field": "upstream.status",
                        "reason": "non-2xx response",
                        "status": response.status_code,
                    }
                ],
            )

        _validate_output(body, spec)
        return ToolCallResult(
            status="SUCCESS",
            output=body,
            latency_ms=latency_ms,
            request_digest=request_digest,
            response_digest=config_hash(body),
        )

    async def _execute_mcp(
        self,
        *,
        actor: ActorContext,
        spec: dict[str, Any],
        arguments: dict[str, Any],
        headers: dict[str, str],
    ) -> ToolCallResult:
        url = spec["url"]
        transport = spec.get("transport", "streamable_http")
        tool_name = spec["tool_name"]
        timeout_s = float(spec.get("timeout_s", 15))
        request_digest = config_hash(
            {"transport": transport, "url": url, "tool_name": tool_name, "arguments": arguments}
        )

        started = perf_counter()
        try:
            result = await self._mcp_session.call_tool(
                url=url,
                transport=transport,
                tool_name=tool_name,
                arguments=arguments,
                headers=headers,
                timeout_s=timeout_s,
            )
        except TimeoutError as exc:
            raise ToolTimeoutError(
                f"mcp tool call exceeded {timeout_s}s",
                details=[{"field": "timeout_s", "reason": "remote server did not respond in time"}],
            ) from exc
        except Exception as exc:  # noqa: BLE001 - 传输/协议错误统一收敛为 MCP 工具错误
            raise ToolMcpError(remote_tool_name=tool_name) from exc

        latency_ms = max(int((perf_counter() - started) * 1000), 0)
        if result.is_error:
            raise ToolMcpError(
                remote_tool_name=result.remote_tool_name, remote_error=result.error_summary
            )

        _validate_output(result.output, spec)
        return ToolCallResult(
            status="SUCCESS",
            output=result.output,
            latency_ms=latency_ms,
            request_digest=request_digest,
            response_digest=config_hash(result.output),
        )


def _validate_arguments(arguments: dict[str, Any], spec: dict[str, Any]) -> None:
    input_schema = spec.get("input_schema")
    if not input_schema:
        return
    try:
        jsonschema_validate(instance=arguments, schema=input_schema)
    except JsonSchemaValidationError as exc:
        raise ValidationFailedError(
            details=[{"field": f"arguments{_pointer(exc.path)}", "reason": exc.message}]
        ) from exc


def _validate_output(output: Any, spec: dict[str, Any]) -> None:
    output_schema = spec.get("output_schema")
    if not output_schema:
        return
    try:
        jsonschema_validate(instance=output, schema=output_schema)
    except JsonSchemaValidationError as exc:
        raise ToolOutputInvalidError(
            details=[{"field": f"output{_pointer(exc.path)}", "reason": exc.message}]
        ) from exc


def _parse_body(response: httpx.Response) -> Any:
    content_type = response.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            return response.json()
        except ValueError:
            return {"raw": response.text}
    return {"raw": response.text}


def _pointer(path) -> str:
    parts = [str(item) for item in path]
    return "".join(f".{part}" for part in parts)


tool_service = ToolService()
