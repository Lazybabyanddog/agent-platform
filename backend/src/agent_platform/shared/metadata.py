"""聚合所有上下文的表定义，供 Alembic 使用。

新增上下文时只需在此 import 其 tables 模块，表才会进入 metadata。
"""

from __future__ import annotations

# import 顺序无关，但不能省略：仅注册元数据，不产生运行时依赖
from agent_platform.contexts.agent.infrastructure import tables as _agent_tables  # noqa: F401
from agent_platform.contexts.catalog.infrastructure import tables as _catalog_tables  # noqa: F401
from agent_platform.contexts.execution.infrastructure import tables as _execution_tables  # noqa: F401
from agent_platform.contexts.insight.infrastructure import tables as _insight_tables  # noqa: F401
from agent_platform.contexts.platform.infrastructure import tables as _platform_tables  # noqa: F401
from agent_platform.shared.base import Base

metadata = Base.metadata
