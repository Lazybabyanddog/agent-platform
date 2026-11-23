"""数据库引擎、会话与租户上下文注入。

约定：
- 应用使用非超级用户连接，PostgreSQL RLS 才能真正拦截；
- 每个事务开始时用 ``set_config(..., is_local=true)`` 注入租户，事务结束自动失效，
  不会被连接池带到下一个请求；
- 没有租户上下文时查询返回空集（RLS 策略兜底），应用层仍需显式带过滤条件。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from agent_platform.settings import get_settings
from agent_platform.shared.context import current_tenant_id

_app_engine: AsyncEngine | None = None
_migration_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _app_engine
    if _app_engine is None:
        settings = get_settings()
        _app_engine = create_async_engine(
            settings.database_url,
            echo=settings.db_echo,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_pre_ping=True,
        )
    return _app_engine


def get_migration_engine() -> AsyncEngine:
    """迁移与引导使用的连接串（超级用户）。"""
    global _migration_engine
    if _migration_engine is None:
        settings = get_settings()
        _migration_engine = create_async_engine(
            settings.migration_database_url,
            echo=settings.db_echo,
            pool_size=2,
            max_overflow=2,
        )
    return _migration_engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            bind=get_engine(),
            expire_on_commit=False,
            autoflush=False,
        )
    return _sessionmaker


async def apply_tenant(session: AsyncSession, tenant_id: uuid.UUID | None) -> None:
    """在事务内注入租户上下文；tenant_id 为 None 时显式清空。"""
    await session.execute(
        text("select set_config('app.tenant_id', :tid, true)"),
        {"tid": str(tenant_id) if tenant_id else ""},
    )


@asynccontextmanager
async def session_scope(tenant_id: uuid.UUID | None = None) -> AsyncIterator[AsyncSession]:
    """开启一个带租户上下文的事务。

    未显式传 tenant_id 时取当前请求的租户；两者都没有则不注入，
    此时 RLS 会让所有租户表查询返回空集。
    """
    factory = get_sessionmaker()
    effective_tenant = tenant_id or current_tenant_id()
    async with factory() as session:
        async with session.begin():
            await apply_tenant(session, effective_tenant)
            yield session


@asynccontextmanager
async def system_session_scope() -> AsyncIterator[AsyncSession]:
    """平台级事务（迁移、引导、跨租户运维），使用超级用户连接。"""
    factory = async_sessionmaker(bind=get_migration_engine(), expire_on_commit=False, autoflush=False)
    async with factory() as session:
        async with session.begin():
            yield session


async def dispose_engines() -> None:
    global _app_engine, _migration_engine, _sessionmaker
    if _app_engine is not None:
        await _app_engine.dispose()
        _app_engine = None
    if _migration_engine is not None:
        await _migration_engine.dispose()
        _migration_engine = None
    _sessionmaker = None
