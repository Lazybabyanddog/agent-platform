"""应用配置的唯一来源。

所有可调参数都从这里读取；业务代码不得直接访问 os.environ。
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_JWT_SECRET = "dev-only-insecure-jwt-secret-change-me"
DEV_ENC_KEY = "dev-only-32-byte-key-for-tests!!"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "agent-platform"
    environment: Literal["dev", "test", "prod"] = "dev"
    debug: bool = False
    api_prefix: str = "/api/v1"

    # PostgreSQL：应用用非超级用户（RLS 生效），迁移与引导用超级用户
    database_url: str = "postgresql+asyncpg://agent_app:agent_app@localhost:5432/agent_platform"
    migration_database_url: str = "postgresql+asyncpg://agent:agent@localhost:5432/agent_platform"
    db_pool_size: int = 10
    db_max_overflow: int = 10
    db_echo: bool = False

    # Redis（限流、缓存、短期锁）
    redis_url: str = "redis://localhost:6380/0"

    # RabbitMQ（事件发布）
    rabbitmq_url: str = "amqp://agent:agent@localhost:5672/"
    events_exchange: str = "agent_platform.events"
    outbox_poll_interval_s: float = 1.0
    outbox_batch_size: int = 50
    outbox_publisher_enabled: bool = True

    # 认证与凭据
    jwt_secret: str = DEV_JWT_SECRET
    jwt_algorithm: str = "HS256"
    jwt_ttl_seconds: int = 8 * 3600
    secret_enc_key: str = DEV_ENC_KEY
    api_key_prefix: str = "ap"

    # 执行默认值（可被 Agent 版本策略覆盖）
    sync_max_timeout_s: int = 120
    default_max_steps: int = 12
    default_timeout_s: int = 90
    default_token_budget: int = 20000

    # 模型调用
    model_probe_timeout_s: float = 10.0
    default_model_rpm_limit: int = 600
    default_model_concurrency_limit: int = 20

    # 可观测
    log_level: str = "INFO"
    log_json: bool = True
    # 空表示只写 stdout（容器默认）；设置目录后额外落 <dir>/<service>.log
    log_dir: str | None = None
    log_max_bytes: int = 10 * 1024 * 1024
    log_backup_count: int = 5
    retain_prompt_content: bool = False

    @field_validator("log_dir", mode="before")
    @classmethod
    def _blank_log_dir_is_none(cls, value: object) -> object:
        """``LOG_DIR=`` 的空值等同于不配置，避免把当前目录当成日志目录。"""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def _check_production_secrets(self) -> Settings:
        if self.environment != "prod":
            return self
        if self.jwt_secret == DEV_JWT_SECRET:
            raise ValueError("JWT_SECRET must be set to a non-default value when ENVIRONMENT=prod")
        if self.secret_enc_key == DEV_ENC_KEY:
            raise ValueError("SECRET_ENC_KEY must be set to a non-default value when ENVIRONMENT=prod")
        return self

    @property
    def is_test(self) -> bool:
        return self.environment == "test"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
