"""日志管道：structlog 渲染 + 标准库桥接。

字段契约固定为 ``timestamp`` / ``level`` / ``event`` / ``service`` / ``environment``
以及 ``trace_id`` / ``request_id`` / ``tenant_id`` / ``run_id``；后四个没有上下文时
显式输出 ``None``，保证下游按字段过滤时不会漏掉记录。

输出始终包含 stdout；配置了 ``LOG_DIR`` 时再按 ``<service>.log`` 落盘并按大小轮转
（API 与 outbox 各写各的文件，不会互相交错）。未配置目录时等同于纯 stdout，
容器部署不需要额外设置。
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

import structlog

from agent_platform.settings import Settings

CONTEXT_FIELDS = ("trace_id", "request_id", "tenant_id", "run_id")
CONTRACT_FIELDS = ("timestamp", "level", "event", "service", "environment", *CONTEXT_FIELDS)

# (level, json, log_dir, service) —— 值未变则不重复配置
_state: tuple[str, bool, str | None, str] | None = None
_service = "api"
_environment = "dev"
_our_handlers: list[logging.Handler] = []


def _ensure_contract(_logger: Any, _name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """补齐进程级字段与缺失的上下文键。"""
    if not event_dict.get("event"):
        event_dict["event"] = event_dict.get("logger") or "log"
    event_dict.setdefault("service", _service)
    event_dict.setdefault("environment", _environment)
    for field in CONTEXT_FIELDS:
        if field not in event_dict:
            event_dict[field] = None
    return event_dict


def _foreign_event(_logger: Any, _name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """标准库记录：``event`` 用 logger 名，原始文本降为 ``message``。"""
    record = event_dict.get("_record")
    logger_name = getattr(record, "name", None) or _name or "stdlib"
    event_dict["message"] = event_dict.get("event")
    event_dict["event"] = logger_name
    return event_dict


class _StdoutHandler(logging.StreamHandler):
    """始终写当前 ``sys.stdout``：测试捕获与运行期重定向都能生效。"""

    def __init__(self) -> None:
        super().__init__(stream=sys.stdout)

    @property
    def stream(self) -> Any:  # type: ignore[override]
        return sys.stdout

    @stream.setter
    def stream(self, _value: Any) -> None:  # noqa: ARG002 - 屏蔽基类的初始赋值
        pass


class _SafeRotatingFileHandler(RotatingFileHandler):
    """写入失败时静默降级：日志故障不得改变业务结果。"""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            super().emit(record)
        except Exception:  # noqa: BLE001 - 磁盘满/句柄失效都不该反噬调用方
            pass


def _close_handlers() -> None:
    root = logging.getLogger()
    for handler in _our_handlers:
        root.removeHandler(handler)
        try:
            handler.close()
        except Exception:  # noqa: BLE001 - 关闭失败无需处理
            pass
    _our_handlers.clear()


def configure_logging(settings: Settings, *, service: str = "api") -> None:
    """建立 stdout + 可选文件的单一管道；重复调用按配置值幂等。"""
    global _state, _service, _environment

    level_name = settings.log_level.upper()
    level = getattr(logging, level_name, logging.INFO)
    state = (level_name, bool(settings.log_json), settings.log_dir, service)
    if _state == state:
        return

    _close_handlers()
    structlog.reset_defaults()
    _service = service
    _environment = settings.environment

    pre_chain: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True, key="timestamp"),
        _ensure_contract,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    structlog.configure(
        processors=[*pre_chain, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    renderer: Any = (
        structlog.processors.JSONRenderer()
        if settings.log_json
        else structlog.dev.ConsoleRenderer(colors=False)
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=[_foreign_event, *pre_chain],
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    root = logging.getLogger()
    root.setLevel(level)

    stdout_handler = _StdoutHandler()
    stdout_handler.setFormatter(formatter)
    root.addHandler(stdout_handler)
    _our_handlers.append(stdout_handler)

    file_error: Exception | None = None
    if settings.log_dir:
        try:
            directory = Path(settings.log_dir).expanduser()
            directory.mkdir(parents=True, exist_ok=True)
            file_handler = _SafeRotatingFileHandler(
                directory / f"{service}.log",
                maxBytes=settings.log_max_bytes,
                backupCount=settings.log_backup_count,
                encoding="utf-8",
                delay=True,
            )
            file_handler.setFormatter(formatter)
            root.addHandler(file_handler)
            _our_handlers.append(file_handler)
        except Exception as exc:  # noqa: BLE001 - 落盘是增强能力，失败只降级
            file_error = exc

    _take_over("uvicorn", level)
    _take_over("uvicorn.error", level)
    _take_over("uvicorn.access", level)

    _state = state
    if file_error is not None:
        get_logger("agent_platform.logging").warning(
            "logging.degraded",
            log_dir=settings.log_dir,
            reason=type(file_error).__name__,
            detail=str(file_error),
        )


def _take_over(name: str, level: int) -> None:
    """让 uvicorn 自己的 logger 走同一管道，而不是另起一套格式。"""
    logger = logging.getLogger(name)
    logger.handlers.clear()
    logger.propagate = True
    logger.setLevel(level)


def get_logger(name: str = "agent_platform"):
    """业务侧唯一入口；返回 stdlib 后端 logger，与标准库日志汇入同一渲染器。"""
    return structlog.get_logger(name)


def reset_logging() -> None:
    """拆除管道，主要供测试在多套配置之间切换。"""
    global _state, _service, _environment
    _close_handlers()
    structlog.reset_defaults()
    structlog.contextvars.clear_contextvars()
    _state = None
    _service = "api"
    _environment = "dev"
