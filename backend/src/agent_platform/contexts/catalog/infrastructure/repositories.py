from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_platform.contexts.catalog.infrastructure.tables import (
    Model,
    ModelProbe,
    ModelProvider,
    Prompt,
    PromptVersion,
    Tool,
    ToolGrant,
    ToolVersion,
)


class ProviderRepository:
    async def by_id(
        self, session: AsyncSession, *, tenant_id: uuid.UUID, provider_id: uuid.UUID
    ) -> ModelProvider | None:
        stmt = select(ModelProvider).where(ModelProvider.id == provider_id)
        stmt = stmt.where(
            (ModelProvider.tenant_id == tenant_id) | (ModelProvider.tenant_id.is_(None))
        )
        return await session.scalar(stmt)

    async def list(self, session: AsyncSession, *, tenant_id: uuid.UUID) -> list[ModelProvider]:
        stmt = select(ModelProvider).where(
            (ModelProvider.tenant_id == tenant_id) | (ModelProvider.tenant_id.is_(None))
        )
        return list(await session.scalars(stmt.order_by(ModelProvider.name)))

    async def create(
        self, session: AsyncSession, *, tenant_id: uuid.UUID, **values: Any
    ) -> ModelProvider:
        provider = ModelProvider(tenant_id=tenant_id, **values)
        session.add(provider)
        await session.flush()
        return provider


class ModelRepository:
    async def by_id(
        self, session: AsyncSession, *, tenant_id: uuid.UUID, model_id: uuid.UUID
    ) -> Model | None:
        return await session.scalar(
            select(Model).where(Model.tenant_id == tenant_id, Model.id == model_id)
        )

    async def by_code(
        self, session: AsyncSession, *, tenant_id: uuid.UUID, code: str
    ) -> Model | None:
        return await session.scalar(
            select(Model).where(Model.tenant_id == tenant_id, Model.code == code)
        )

    async def list(self, session: AsyncSession, *, tenant_id: uuid.UUID) -> list[Model]:
        return list(
            await session.scalars(
                select(Model).where(Model.tenant_id == tenant_id).order_by(Model.code)
            )
        )

    async def create(self, session: AsyncSession, *, tenant_id: uuid.UUID, **values: Any) -> Model:
        model = Model(tenant_id=tenant_id, **values)
        session.add(model)
        await session.flush()
        return model


class ProbeRepository:
    """探测历史：供应商级与模型级共用一张表，按 model_id 是否为空区分。"""

    async def create(
        self, session: AsyncSession, *, tenant_id: uuid.UUID, **values: Any
    ) -> ModelProbe:
        probe = ModelProbe(tenant_id=tenant_id, **values)
        session.add(probe)
        await session.flush()
        return probe

    async def latest_by_provider(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        provider_ids: list[uuid.UUID],
    ) -> dict[uuid.UUID, ModelProbe]:
        """供应商级探测的最新一条（model_id 为空）。"""
        if not provider_ids:
            return {}
        rows = await session.scalars(
            select(ModelProbe)
            .where(
                ModelProbe.tenant_id == tenant_id,
                ModelProbe.provider_id.in_(provider_ids),
                ModelProbe.model_id.is_(None),
            )
            .order_by(ModelProbe.created_at.desc())
        )
        latest: dict[uuid.UUID, ModelProbe] = {}
        for row in rows:
            latest.setdefault(row.provider_id, row)
        return latest

    async def latest_for_model(
        self, session: AsyncSession, *, tenant_id: uuid.UUID, model_id: uuid.UUID
    ) -> ModelProbe | None:
        return await session.scalar(
            select(ModelProbe)
            .where(ModelProbe.tenant_id == tenant_id, ModelProbe.model_id == model_id)
            .order_by(ModelProbe.created_at.desc())
            .limit(1)
        )

    async def list_for_provider(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        provider_id: uuid.UUID,
        limit: int = 20,
    ) -> list[ModelProbe]:
        return list(
            await session.scalars(
                select(ModelProbe)
                .where(ModelProbe.tenant_id == tenant_id, ModelProbe.provider_id == provider_id)
                .order_by(ModelProbe.created_at.desc())
                .limit(limit)
            )
        )

    async def list_for_model(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        model_id: uuid.UUID,
        limit: int = 20,
    ) -> list[ModelProbe]:
        return list(
            await session.scalars(
                select(ModelProbe)
                .where(ModelProbe.tenant_id == tenant_id, ModelProbe.model_id == model_id)
                .order_by(ModelProbe.created_at.desc())
                .limit(limit)
            )
        )


class PromptRepository:
    async def by_id(
        self, session: AsyncSession, *, tenant_id: uuid.UUID, prompt_id: uuid.UUID
    ) -> Prompt | None:
        return await session.scalar(
            select(Prompt).where(Prompt.tenant_id == tenant_id, Prompt.id == prompt_id)
        )

    async def by_code(
        self, session: AsyncSession, *, tenant_id: uuid.UUID, code: str
    ) -> Prompt | None:
        return await session.scalar(
            select(Prompt).where(Prompt.tenant_id == tenant_id, Prompt.code == code)
        )

    async def list(self, session: AsyncSession, *, tenant_id: uuid.UUID) -> list[Prompt]:
        return list(
            await session.scalars(
                select(Prompt).where(Prompt.tenant_id == tenant_id).order_by(Prompt.code)
            )
        )

    async def create(self, session: AsyncSession, *, tenant_id: uuid.UUID, **values: Any) -> Prompt:
        prompt = Prompt(tenant_id=tenant_id, **values)
        session.add(prompt)
        await session.flush()
        return prompt


class PromptVersionRepository:
    async def by_id(
        self, session: AsyncSession, *, tenant_id: uuid.UUID, version_id: uuid.UUID
    ) -> PromptVersion | None:
        return await session.scalar(
            select(PromptVersion).where(
                PromptVersion.tenant_id == tenant_id, PromptVersion.id == version_id
            )
        )

    async def by_no(
        self, session: AsyncSession, *, prompt_id: uuid.UUID, version_no: int
    ) -> PromptVersion | None:
        return await session.scalar(
            select(PromptVersion).where(
                PromptVersion.prompt_id == prompt_id, PromptVersion.version_no == version_no
            )
        )

    async def list_for_prompt(
        self, session: AsyncSession, *, prompt_id: uuid.UUID
    ) -> list[PromptVersion]:
        return list(
            await session.scalars(
                select(PromptVersion)
                .where(PromptVersion.prompt_id == prompt_id)
                .order_by(PromptVersion.version_no.desc())
            )
        )

    async def latest(self, session: AsyncSession, *, prompt_id: uuid.UUID) -> PromptVersion | None:
        return await session.scalar(
            select(PromptVersion)
            .where(PromptVersion.prompt_id == prompt_id)
            .order_by(PromptVersion.version_no.desc())
            .limit(1)
        )

    async def next_version_no(self, session: AsyncSession, *, prompt_id: uuid.UUID) -> int:
        current = await session.scalar(
            select(func.max(PromptVersion.version_no)).where(PromptVersion.prompt_id == prompt_id)
        )
        return int(current or 0) + 1

    async def create(
        self, session: AsyncSession, *, tenant_id: uuid.UUID, **values: Any
    ) -> PromptVersion:
        version = PromptVersion(tenant_id=tenant_id, **values)
        session.add(version)
        await session.flush()
        return version


class ToolRepository:
    async def by_id(
        self, session: AsyncSession, *, tenant_id: uuid.UUID, tool_id: uuid.UUID
    ) -> Tool | None:
        return await session.scalar(
            select(Tool).where(Tool.tenant_id == tenant_id, Tool.id == tool_id)
        )

    async def by_code(
        self, session: AsyncSession, *, tenant_id: uuid.UUID, code: str
    ) -> Tool | None:
        return await session.scalar(
            select(Tool).where(Tool.tenant_id == tenant_id, Tool.code == code)
        )

    async def list(self, session: AsyncSession, *, tenant_id: uuid.UUID) -> list[Tool]:
        return list(
            await session.scalars(
                select(Tool).where(Tool.tenant_id == tenant_id).order_by(Tool.code)
            )
        )

    async def create(self, session: AsyncSession, *, tenant_id: uuid.UUID, **values: Any) -> Tool:
        tool = Tool(tenant_id=tenant_id, **values)
        session.add(tool)
        await session.flush()
        return tool


class ToolVersionRepository:
    async def by_id(
        self, session: AsyncSession, *, tenant_id: uuid.UUID, version_id: uuid.UUID
    ) -> ToolVersion | None:
        return await session.scalar(
            select(ToolVersion).where(
                ToolVersion.tenant_id == tenant_id, ToolVersion.id == version_id
            )
        )

    async def by_no(
        self, session: AsyncSession, *, tool_id: uuid.UUID, version_no: int
    ) -> ToolVersion | None:
        return await session.scalar(
            select(ToolVersion).where(
                ToolVersion.tool_id == tool_id, ToolVersion.version_no == version_no
            )
        )

    async def latest(self, session: AsyncSession, *, tool_id: uuid.UUID) -> ToolVersion | None:
        return await session.scalar(
            select(ToolVersion)
            .where(ToolVersion.tool_id == tool_id)
            .order_by(ToolVersion.version_no.desc())
            .limit(1)
        )

    async def latest_for_tools(
        self, session: AsyncSession, *, tool_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, ToolVersion]:
        """批量取每个工具的最高版本；一次查询完成，避免逐工具 N+1。"""
        if not tool_ids:
            return {}
        stmt = (
            select(ToolVersion)
            .where(ToolVersion.tool_id.in_(tool_ids))
            .order_by(ToolVersion.tool_id, ToolVersion.version_no.desc())
            .distinct(ToolVersion.tool_id)
        )
        versions = await session.scalars(stmt)
        return {version.tool_id: version for version in versions}

    async def list_for_tool(
        self, session: AsyncSession, *, tool_id: uuid.UUID
    ) -> list[ToolVersion]:
        return list(
            await session.scalars(
                select(ToolVersion)
                .where(ToolVersion.tool_id == tool_id)
                .order_by(ToolVersion.version_no.desc())
            )
        )

    async def next_version_no(self, session: AsyncSession, *, tool_id: uuid.UUID) -> int:
        current = await session.scalar(
            select(func.max(ToolVersion.version_no)).where(ToolVersion.tool_id == tool_id)
        )
        return int(current or 0) + 1

    async def create(
        self, session: AsyncSession, *, tenant_id: uuid.UUID, **values: Any
    ) -> ToolVersion:
        version = ToolVersion(tenant_id=tenant_id, **values)
        session.add(version)
        await session.flush()
        return version


class ToolGrantRepository:
    async def get(
        self, session: AsyncSession, *, tenant_id: uuid.UUID, tool_id: uuid.UUID
    ) -> ToolGrant | None:
        return await session.scalar(
            select(ToolGrant).where(ToolGrant.tenant_id == tenant_id, ToolGrant.tool_id == tool_id)
        )

    async def list(self, session: AsyncSession, *, tenant_id: uuid.UUID) -> list[ToolGrant]:
        return list(
            await session.scalars(
                select(ToolGrant).where(ToolGrant.tenant_id == tenant_id).order_by(ToolGrant.created_at)
            )
        )

    async def create(
        self, session: AsyncSession, *, tenant_id: uuid.UUID, tool_id: uuid.UUID, granted_by: uuid.UUID | None
    ) -> ToolGrant:
        grant = ToolGrant(tenant_id=tenant_id, tool_id=tool_id, granted_by=granted_by)
        session.add(grant)
        await session.flush()
        return grant

    async def delete(self, session: AsyncSession, *, tenant_id: uuid.UUID, tool_id: uuid.UUID) -> int:
        grant = await self.get(session, tenant_id=tenant_id, tool_id=tool_id)
        if grant is None:
            return 0
        await session.delete(grant)
        return 1
