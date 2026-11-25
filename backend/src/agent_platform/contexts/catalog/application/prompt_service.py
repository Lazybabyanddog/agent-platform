"""Prompt 与其版本。

版本是只读的：新增版本 = 新行，编辑旧版本没有入口；对比与回滚都基于版本号，
并发新增由 (prompt_id, version_no) 唯一约束兜底，冲突返回 409。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.exc import IntegrityError

from agent_platform.contexts.catalog.domain.render import (
    render_messages,
    validate_messages_variables,
)
from agent_platform.contexts.catalog.infrastructure.repositories import (
    PromptRepository,
    PromptVersionRepository,
)
from agent_platform.contexts.catalog.infrastructure.tables import Prompt, PromptVersion
from agent_platform.contexts.platform.application.audit_service import audit_service
from agent_platform.shared.context import ActorContext
from agent_platform.shared.db import session_scope
from agent_platform.shared.errors import ConflictError, NotFoundError
from agent_platform.shared.hashing import config_hash


@dataclass(frozen=True, slots=True)
class VersionDiff:
    base_version_no: int
    target_version_no: int
    identical: bool
    message_changes: list[dict[str, Any]] = field(default_factory=list)
    variables_added: list[str] = field(default_factory=list)
    variables_removed: list[str] = field(default_factory=list)
    variables_changed: list[dict[str, Any]] = field(default_factory=list)


class PromptService:
    async def create(
        self,
        *,
        actor: ActorContext,
        code: str,
        name: str,
        description: str | None = None,
        messages: list[dict[str, Any]] | None = None,
        variables: list[dict[str, Any]] | None = None,
        note: str | None = None,
    ) -> tuple[Prompt, PromptVersion | None]:
        async with session_scope(actor.tenant_id) as session:
            repo = PromptRepository()
            if await repo.by_code(session, tenant_id=actor.tenant_id, code=code):
                raise ConflictError(f"prompt code {code!r} already exists")
            prompt = await repo.create(
                session,
                tenant_id=actor.tenant_id,
                code=code,
                name=name,
                description=description,
            )
            version = None
            if messages:
                validate_messages_variables(messages, variables or [])
                version = await self._create_version_row(
                    session,
                    tenant_id=actor.tenant_id,
                    prompt_id=prompt.id,
                    messages=messages,
                    variables=variables or [],
                    note=note,
                    actor=actor,
                )
            return prompt, version

    async def list(self, *, actor: ActorContext) -> list[Prompt]:
        async with session_scope(actor.tenant_id) as session:
            return await PromptRepository().list(session, tenant_id=actor.tenant_id)

    async def get(self, *, actor: ActorContext, prompt_id: uuid.UUID) -> Prompt:
        async with session_scope(actor.tenant_id) as session:
            prompt = await PromptRepository().by_id(
                session, tenant_id=actor.tenant_id, prompt_id=prompt_id
            )
            if prompt is None:
                raise NotFoundError("prompt not found")
            return prompt

    async def delete(self, *, actor: ActorContext, prompt_id: uuid.UUID) -> None:
        """删除 Prompt 与其全部版本；已被 Agent 版本引用时拒绝，先解引用再删。"""
        async with session_scope(actor.tenant_id) as session:
            repo = PromptRepository()
            prompt = await repo.by_id(
                session, tenant_id=actor.tenant_id, prompt_id=prompt_id
            )
            if prompt is None:
                raise NotFoundError("prompt not found")
            references = await repo.count_agent_version_references(
                session, tenant_id=actor.tenant_id, prompt_id=prompt_id
            )
            if references:
                raise ConflictError(
                    "prompt is still referenced by agent versions",
                    code="PROMPT_IN_USE",
                    details=[
                        {
                            "field": "prompt_id",
                            "reason": "detach the prompt from agent versions first",
                            "agent_version_count": references,
                        }
                    ],
                )
            await audit_service.record(
                session,
                tenant_id=actor.tenant_id,
                action="prompt.deleted",
                resource_type="prompt",
                resource_id=prompt.id,
                actor=actor,
                detail={"code": prompt.code, "name": prompt.name},
            )
            await repo.delete(session, prompt=prompt)

    async def add_version(
        self,
        *,
        actor: ActorContext,
        prompt_id: uuid.UUID,
        messages: list[dict[str, Any]],
        variables: list[dict[str, Any]],
        note: str | None = None,
    ) -> PromptVersion:
        validate_messages_variables(messages, variables)
        async with session_scope(actor.tenant_id) as session:
            prompt = await PromptRepository().by_id(
                session, tenant_id=actor.tenant_id, prompt_id=prompt_id
            )
            if prompt is None:
                raise NotFoundError("prompt not found")
            return await self._create_version_row(
                session,
                tenant_id=actor.tenant_id,
                prompt_id=prompt_id,
                messages=messages,
                variables=variables,
                note=note,
                actor=actor,
            )

    async def list_versions(
        self, *, actor: ActorContext, prompt_id: uuid.UUID
    ) -> list[PromptVersion]:
        async with session_scope(actor.tenant_id) as session:
            return await PromptVersionRepository().list_for_prompt(session, prompt_id=prompt_id)

    async def get_version(
        self, *, actor: ActorContext, version_id: uuid.UUID
    ) -> PromptVersion:
        async with session_scope(actor.tenant_id) as session:
            version = await PromptVersionRepository().by_id(
                session, tenant_id=actor.tenant_id, version_id=version_id
            )
            if version is None:
                raise NotFoundError("prompt version not found")
            return version

    async def diff(
        self, *, actor: ActorContext, base_version_id: uuid.UUID, target_version_id: uuid.UUID
    ) -> VersionDiff:
        async with session_scope(actor.tenant_id) as session:
            repo = PromptVersionRepository()
            base = await repo.by_id(session, tenant_id=actor.tenant_id, version_id=base_version_id)
            target = await repo.by_id(
                session, tenant_id=actor.tenant_id, version_id=target_version_id
            )
            if base is None or target is None:
                raise NotFoundError("prompt version not found")
            return _diff_versions(base, target)

    async def preview(
        self,
        *,
        actor: ActorContext,
        version_id: uuid.UUID,
        values: dict[str, Any] | None,
    ) -> list[dict[str, str]]:
        version = await self.get_version(actor=actor, version_id=version_id)
        return render_messages(version.messages, version.variables, values)

    # ------------------------------------------------------------------

    async def _create_version_row(
        self,
        session,
        *,
        tenant_id: uuid.UUID,
        prompt_id: uuid.UUID,
        messages: list[dict[str, Any]],
        variables: list[dict[str, Any]],
        note: str | None,
        actor: ActorContext,
    ) -> PromptVersion:
        repo = PromptVersionRepository()
        version_no = await repo.next_version_no(session, prompt_id=prompt_id)
        try:
            async with session.begin_nested():
                return await repo.create(
                    session,
                    tenant_id=tenant_id,
                    prompt_id=prompt_id,
                    version_no=version_no,
                    messages=messages,
                    variables=variables,
                    config_hash=config_hash({"messages": messages, "variables": variables}),
                    note=note,
                    created_by=actor.actor_id,
                )
        except IntegrityError as exc:
            raise ConflictError("prompt version conflict, retry the request") from exc


def _diff_versions(base: PromptVersion, target: PromptVersion) -> VersionDiff:
    changes: list[dict[str, Any]] = []
    base_messages, target_messages = base.messages, target.messages
    for index in range(max(len(base_messages), len(target_messages))):
        before = base_messages[index] if index < len(base_messages) else None
        after = target_messages[index] if index < len(target_messages) else None
        if before != after:
            changes.append({"index": index, "base": before, "target": after})

    base_vars = {item["name"]: item for item in base.variables}
    target_vars = {item["name"]: item for item in target.variables}
    added = sorted(set(target_vars) - set(base_vars))
    removed = sorted(set(base_vars) - set(target_vars))
    changed = [
        {"name": name, "base": base_vars[name], "target": target_vars[name]}
        for name in sorted(set(base_vars) & set(target_vars))
        if base_vars[name] != target_vars[name]
    ]

    return VersionDiff(
        base_version_no=base.version_no,
        target_version_no=target.version_no,
        identical=base.config_hash == target.config_hash,
        message_changes=changes,
        variables_added=added,
        variables_removed=removed,
        variables_changed=changed,
    )


prompt_service = PromptService()
