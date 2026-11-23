"""模型与供应商的用例。

- ``code`` 是租户内稳定标识（Agent 版本快照引用它），``model_name`` 才发给上游；
- 参数模板在写入时就校验，避免上线后才发现配置非法；
- 连通性探测只用只读的 /models 接口，并且不记录任何凭据内容。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import datetime
from time import perf_counter
from typing import Any

import httpx

from agent_platform.contexts.catalog.domain.enums import ModelStatus, ProbeVerdict, ProviderStatus
from agent_platform.contexts.catalog.domain.params import validate_template
from agent_platform.contexts.catalog.infrastructure.repositories import (
    ModelRepository,
    ProbeRepository,
    ProviderRepository,
)
from agent_platform.contexts.catalog.infrastructure.tables import Model, ModelProbe, ModelProvider
from agent_platform.contexts.platform.application.audit_service import audit_service
from agent_platform.contexts.platform.application.secret_service import secret_service
from agent_platform.settings import get_settings
from agent_platform.shared.context import ActorContext
from agent_platform.shared.db import session_scope
from agent_platform.shared.errors import (
    ConflictError,
    NotFoundError,
    ResourceUnavailableError,
    ValidationFailedError,
)

# 兼容既有调用方的字符串常量；取值全部来自 ProbeVerdict，响应与落库保持一致
PROBE_OK = ProbeVerdict.OK.value
PROBE_MODEL_NOT_FOUND = ProbeVerdict.MODEL_NOT_FOUND.value
PROBE_CREDENTIAL_OK_MODEL_UNVERIFIED = ProbeVerdict.CREDENTIAL_OK_MODEL_UNVERIFIED.value
PROBE_AUTH_FAILED = ProbeVerdict.MODEL_PROBE_AUTH_FAILED.value
PROBE_TIMEOUT = ProbeVerdict.MODEL_PROBE_TIMEOUT.value
PROBE_UNREACHABLE = ProbeVerdict.MODEL_PROBE_UNREACHABLE.value
# 其它 HTTP 错误按"无法连通"归类，六态里不再单列
PROBE_HTTP_ERROR = ProbeVerdict.MODEL_PROBE_UNREACHABLE.value

# 上游清单可能很长，响应与历史只保留前 N 个标识；命中标识单独存，不受截断影响
PROBE_OBSERVED_MODELS_LIMIT = 50
PROBE_OBSERVED_NAME_MAX = 160


@dataclass(frozen=True, slots=True)
class ProbeResult:
    ok: bool
    status_code: int | None
    latency_ms: int
    model_count: int | None
    error_code: str | None
    verdict: str = PROBE_UNREACHABLE
    observed_models: list[str] | None = None
    observed_model_name: str | None = None
    probe_id: uuid.UUID | None = None
    created_at: datetime | None = None


def _probe_outcome(
    verdict: str,
    status_code: int | None,
    latency_ms: int,
    *,
    model_count: int | None = None,
    observed_models: list[str] | None = None,
    observed_model_name: str | None = None,
) -> ProbeResult:
    return ProbeResult(
        ok=verdict == PROBE_OK,
        status_code=status_code,
        latency_ms=latency_ms,
        model_count=model_count,
        # 兼容既有消费方：error_code 与结论同值，OK 时即为 PROBE_OK
        error_code=verdict,
        verdict=verdict,
        observed_models=observed_models,
        observed_model_name=observed_model_name,
    )


def _observed_models(payload: Any) -> list[str] | None:
    """从 /models 响应里取出上游模型标识；不可解析时返回 None（无法比对）。"""
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return None
    names: list[str] = []
    for item in data:
        if isinstance(item, dict):
            name = item.get("id") or item.get("name")
        elif isinstance(item, str):
            name = item
        else:
            name = None
        if isinstance(name, str) and name:
            names.append(name)
    return names[:PROBE_OBSERVED_MODELS_LIMIT]


def _match_model(model_name: str, observed: list[str]) -> str | None:
    target = model_name.strip().lower()
    for candidate in observed:
        if candidate.strip().lower() == target:
            return candidate
    return None


class ModelService:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    # ---------------------------------------------------------- 供应商

    async def create_provider(
        self,
        *,
        actor: ActorContext,
        name: str,
        base_url: str,
        adapter: str = "openai_compatible",
        secret_ref_id: uuid.UUID | None = None,
        region: str | None = None,
    ) -> ModelProvider:
        if not base_url.startswith(("http://", "https://")):
            raise ValidationFailedError.for_field("base_url", "must start with http:// or https://")
        if secret_ref_id is not None:
            await secret_service.resolve(tenant_id=actor.tenant_id, secret_id=secret_ref_id)
        async with session_scope(actor.tenant_id) as session:
            return await ProviderRepository().create(
                session,
                tenant_id=actor.tenant_id,
                name=name,
                base_url=base_url.rstrip("/"),
                adapter=adapter,
                secret_ref_id=secret_ref_id,
                region=region,
            )

    async def list_providers(self, *, actor: ActorContext) -> list[ModelProvider]:
        async with session_scope(actor.tenant_id) as session:
            return await ProviderRepository().list(session, tenant_id=actor.tenant_id)

    async def latest_provider_probes(
        self, *, actor: ActorContext
    ) -> dict[uuid.UUID, ModelProbe]:
        """供应商级最新探测摘要，供列表并排展示结论与命中标识。"""
        async with session_scope(actor.tenant_id) as session:
            providers = await ProviderRepository().list(session, tenant_id=actor.tenant_id)
            return await ProbeRepository().latest_by_provider(
                session,
                tenant_id=actor.tenant_id,
                provider_ids=[provider.id for provider in providers],
            )

    async def update_provider(
        self,
        *,
        actor: ActorContext,
        provider_id: uuid.UUID,
        changes: dict[str, Any],
    ) -> ModelProvider:
        """变更供应商配置。

        ``base_url`` 与 ``secret_ref_id`` 是活配置：调用时按 ``provider_id`` 重新读取，
        因此这两项一旦变更，引用该供应商的全部已发布 Agent 版本立即受影响
        （``name`` / ``adapter`` / ``region`` / ``status`` 不参与调用）。
        """
        if "base_url" in changes:
            base_url = changes["base_url"]
            if not base_url.startswith(("http://", "https://")):
                raise ValidationFailedError.for_field(
                    "base_url", "must start with http:// or https://"
                )
            changes["base_url"] = base_url.rstrip("/")
        secret_ref_id = changes.get("secret_ref_id")
        if secret_ref_id is not None:
            await secret_service.resolve(tenant_id=actor.tenant_id, secret_id=secret_ref_id)

        async with session_scope(actor.tenant_id) as session:
            provider = await ProviderRepository().by_id(
                session, tenant_id=actor.tenant_id, provider_id=provider_id
            )
            if provider is None:
                raise NotFoundError("model provider not found")

            diff = _apply_changes(provider, changes)
            if diff:
                await audit_service.record(
                    session,
                    tenant_id=actor.tenant_id,
                    action="provider.update",
                    resource_type="model_provider",
                    resource_id=provider.id,
                    actor=actor,
                    detail={"changes": diff},
                )
            return provider

    # ---------------------------------------------------------- 模型

    async def create_model(
        self,
        *,
        actor: ActorContext,
        provider_id: uuid.UUID,
        code: str,
        model_name: str,
        ctx_window: int | None = None,
        modalities: list[str] | None = None,
        capabilities: dict[str, Any] | None = None,
        param_template: dict[str, Any] | None = None,
        price: dict[str, Any] | None = None,
    ) -> Model:
        template = param_template or {}
        validate_template(template)

        async with session_scope(actor.tenant_id) as session:
            provider = await ProviderRepository().by_id(
                session, tenant_id=actor.tenant_id, provider_id=provider_id
            )
            if provider is None:
                raise NotFoundError("model provider not found")
            if provider.status != ProviderStatus.ACTIVE:
                raise ResourceUnavailableError("model provider is disabled")
            if await ModelRepository().by_code(session, tenant_id=actor.tenant_id, code=code):
                raise ConflictError(f"model code {code!r} already exists")
            return await ModelRepository().create(
                session,
                tenant_id=actor.tenant_id,
                provider_id=provider_id,
                code=code,
                model_name=model_name,
                ctx_window=ctx_window,
                modalities=modalities or ["text"],
                capabilities=capabilities or {},
                param_template=template,
                price=price or {},
            )

    async def list_models(self, *, actor: ActorContext) -> list[Model]:
        async with session_scope(actor.tenant_id) as session:
            return await ModelRepository().list(session, tenant_id=actor.tenant_id)

    async def get_model(self, *, actor: ActorContext, model_id: uuid.UUID) -> Model:
        async with session_scope(actor.tenant_id) as session:
            model = await ModelRepository().by_id(
                session, tenant_id=actor.tenant_id, model_id=model_id
            )
            if model is None:
                raise NotFoundError("model not found")
            return model

    async def set_status(
        self, *, actor: ActorContext, model_id: uuid.UUID, status: ModelStatus
    ) -> Model:
        async with session_scope(actor.tenant_id) as session:
            model = await ModelRepository().by_id(
                session, tenant_id=actor.tenant_id, model_id=model_id
            )
            if model is None:
                raise NotFoundError("model not found")
            model.status = status
            return model

    async def update_model(
        self,
        *,
        actor: ActorContext,
        model_id: uuid.UUID,
        changes: dict[str, Any],
    ) -> Model:
        """变更模型的活配置。

        影响范围（与 ``design.md`` D8 一致）：

        - ``model_name`` / ``ctx_window`` / ``modalities`` / ``capabilities`` /
          ``param_template`` / ``price`` 只影响之后生成的 Agent 版本：生成版本时这些值
          随 ``normalize_params`` 冻结进快照，已生成的版本按其快照继续运行；
        - ``base_url`` 与凭据保存在供应商上（本方法不涉及），调用时活取，因此供应商侧
          的变更会立即影响全部已发布版本；
        - ``code`` 与 ``provider_id`` 不可变更：前者被 Agent 版本引用，后者会换掉已发布
          版本的上游供应商，需要更换时新建模型替代。
        """
        if "param_template" in changes:
            validate_template(changes["param_template"])

        async with session_scope(actor.tenant_id) as session:
            model = await ModelRepository().by_id(
                session, tenant_id=actor.tenant_id, model_id=model_id
            )
            if model is None:
                raise NotFoundError("model not found")

            diff = _apply_changes(model, changes, template_keys_only=True)
            if diff:
                await audit_service.record(
                    session,
                    tenant_id=actor.tenant_id,
                    action="model.update",
                    resource_type="model",
                    resource_id=model.id,
                    actor=actor,
                    detail={"changes": diff},
                )
            return model

    # ---------------------------------------------------------- 连通性

    async def probe(self, *, actor: ActorContext, model_id: uuid.UUID) -> ProbeResult:
        """模型级探测：验证上游可达、凭据有效，并比对模型清单是否包含该模型。"""
        async with session_scope(actor.tenant_id) as session:
            model = await ModelRepository().by_id(
                session, tenant_id=actor.tenant_id, model_id=model_id
            )
            if model is None:
                raise NotFoundError("model not found")
            provider = await ProviderRepository().by_id(
                session, tenant_id=actor.tenant_id, provider_id=model.provider_id
            )
            if provider is None:
                raise NotFoundError("model provider not found")
            provider_id = provider.id
            base_url, secret_ref_id, model_name = (
                provider.base_url,
                provider.secret_ref_id,
                model.model_name,
            )

        result = await self._probe_upstream(
            tenant_id=actor.tenant_id,
            base_url=base_url,
            secret_ref_id=secret_ref_id,
            model_name=model_name,
        )
        return await self._record_probe(
            actor=actor, provider_id=provider_id, model_id=model_id, result=result
        )

    async def probe_provider(
        self, *, actor: ActorContext, provider_id: uuid.UUID
    ) -> ProbeResult:
        """供应商级探测：只验证可达与凭据有效（能列出模型清单即视为通过）。"""
        async with session_scope(actor.tenant_id) as session:
            provider = await ProviderRepository().by_id(
                session, tenant_id=actor.tenant_id, provider_id=provider_id
            )
            if provider is None:
                raise NotFoundError("model provider not found")
            base_url, secret_ref_id = provider.base_url, provider.secret_ref_id

        result = await self._probe_upstream(
            tenant_id=actor.tenant_id,
            base_url=base_url,
            secret_ref_id=secret_ref_id,
            model_name=None,
        )
        return await self._record_probe(
            actor=actor, provider_id=provider_id, model_id=None, result=result
        )

    async def provider_probes(
        self, *, actor: ActorContext, provider_id: uuid.UUID, limit: int = 20
    ) -> list[ModelProbe]:
        async with session_scope(actor.tenant_id) as session:
            provider = await ProviderRepository().by_id(
                session, tenant_id=actor.tenant_id, provider_id=provider_id
            )
            if provider is None:
                raise NotFoundError("model provider not found")
            return await ProbeRepository().list_for_provider(
                session, tenant_id=actor.tenant_id, provider_id=provider_id, limit=limit
            )

    async def model_probes(
        self, *, actor: ActorContext, model_id: uuid.UUID, limit: int = 20
    ) -> list[ModelProbe]:
        async with session_scope(actor.tenant_id) as session:
            model = await ModelRepository().by_id(
                session, tenant_id=actor.tenant_id, model_id=model_id
            )
            if model is None:
                raise NotFoundError("model not found")
            return await ProbeRepository().list_for_model(
                session, tenant_id=actor.tenant_id, model_id=model_id, limit=limit
            )

    async def _probe_upstream(
        self,
        *,
        tenant_id: uuid.UUID,
        base_url: str,
        secret_ref_id: uuid.UUID | None,
        model_name: str | None,
    ) -> ProbeResult:
        settings = get_settings()
        headers: dict[str, str] = {}
        if secret_ref_id is not None:
            secret = await secret_service.resolve(tenant_id=tenant_id, secret_id=secret_ref_id)
            headers["Authorization"] = f"Bearer {secret}"

        url = f"{base_url.rstrip('/')}/models"
        started = perf_counter()
        try:
            async with httpx.AsyncClient(
                timeout=settings.model_probe_timeout_s, transport=self._transport
            ) as client:
                response = await client.get(url, headers=headers)
        except httpx.TimeoutException:
            return _probe_outcome(PROBE_TIMEOUT, None, _elapsed_ms(started))
        except httpx.HTTPError:
            return _probe_outcome(PROBE_UNREACHABLE, None, _elapsed_ms(started))

        latency = _elapsed_ms(started)
        if response.status_code in {401, 403}:
            return _probe_outcome(PROBE_AUTH_FAILED, response.status_code, latency)
        if response.status_code >= 400:
            # 其它 HTTP 错误无法区分是网络、代理还是网关问题，统一按"无法连通"处理
            return _probe_outcome(PROBE_UNREACHABLE, response.status_code, latency)

        try:
            payload: Any = response.json()
        except ValueError:
            payload = None
        observed = _observed_models(payload)
        if observed is None:
            return _probe_outcome(
                PROBE_CREDENTIAL_OK_MODEL_UNVERIFIED, response.status_code, latency
            )
        if model_name is None:
            return _probe_outcome(
                PROBE_OK,
                response.status_code,
                latency,
                model_count=len(observed),
                observed_models=observed,
            )
        matched = _match_model(model_name, observed)
        if matched is None:
            return _probe_outcome(
                PROBE_MODEL_NOT_FOUND,
                response.status_code,
                latency,
                model_count=len(observed),
                observed_models=observed,
            )
        return _probe_outcome(
            PROBE_OK,
            response.status_code,
            latency,
            model_count=len(observed),
            observed_models=observed,
            observed_model_name=matched[:PROBE_OBSERVED_NAME_MAX],
        )

    async def _record_probe(
        self,
        *,
        actor: ActorContext,
        provider_id: uuid.UUID,
        model_id: uuid.UUID | None,
        result: ProbeResult,
    ) -> ProbeResult:
        """落库并写审计；detail 只记结论、耗时与状态码，不含凭据。"""
        async with session_scope(actor.tenant_id) as session:
            probe = await ProbeRepository().create(
                session,
                tenant_id=actor.tenant_id,
                provider_id=provider_id,
                model_id=model_id,
                verdict=ProbeVerdict(result.verdict),
                status_code=result.status_code,
                latency_ms=result.latency_ms,
                observed_models=result.observed_models,
                observed_model_name=result.observed_model_name,
                error_code=None if result.verdict == PROBE_OK else result.verdict,
                actor_id=actor.actor_id,
            )
            await audit_service.record(
                session,
                tenant_id=actor.tenant_id,
                action="model.probe",
                resource_type="model" if model_id is not None else "model_provider",
                resource_id=model_id if model_id is not None else provider_id,
                actor=actor,
                detail={
                    "verdict": result.verdict,
                    "latency_ms": result.latency_ms,
                    "status_code": result.status_code,
                },
            )
            return replace(result, probe_id=probe.id, created_at=probe.created_at)


def _apply_changes(
    instance: ModelProvider | Model,
    changes: dict[str, Any],
    *,
    template_keys_only: bool = False,
) -> dict[str, Any]:
    """就地应用变更并返回审计用的旧值/新值；凭据只记标识，不记内容。"""
    diff: dict[str, Any] = {}
    for field, new in changes.items():
        old = getattr(instance, field)
        if old == new:
            continue
        if field == "secret_ref_id":
            diff[field] = {
                "old": None if old is None else str(old),
                "new": None if new is None else str(new),
            }
        elif field == "param_template" and template_keys_only:
            diff[field] = _template_diff(old, new)
        else:
            diff[field] = {"old": old, "new": new}
        setattr(instance, field, new)
    return diff


def _template_diff(old: dict[str, Any] | None, new: dict[str, Any] | None) -> dict[str, Any]:
    """参数模板只记键名与变化摘要，避免把整份配置写进审计。"""
    before = old or {}
    after = new or {}
    before_keys, after_keys = set(before), set(after)
    return {
        "before_keys": sorted(before_keys),
        "after_keys": sorted(after_keys),
        "keys_added": sorted(after_keys - before_keys),
        "keys_removed": sorted(before_keys - after_keys),
        "keys_changed": sorted(
            key for key in before_keys & after_keys if before[key] != after[key]
        ),
    }


def _elapsed_ms(started: float) -> int:
    return max(int((perf_counter() - started) * 1000), 0)


model_service = ModelService()
