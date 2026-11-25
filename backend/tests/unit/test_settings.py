from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_platform.settings import DEV_ENC_KEY, DEV_JWT_SECRET, Settings, get_settings, reset_settings_cache


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch):
    for name in (
        "ENVIRONMENT",
        "DATABASE_URL",
        "SYNC_MAX_TIMEOUT_S",
        "JWT_SECRET",
        "SECRET_ENC_KEY",
        "LOG_JSON",
        "LOG_LEVEL",
        "LOG_DIR",
        "LOG_MAX_BYTES",
        "LOG_BACKUP_COUNT",
    ):
        monkeypatch.delenv(name, raising=False)
    reset_settings_cache()
    yield
    reset_settings_cache()


def test_defaults():
    settings = Settings()
    assert settings.environment == "dev"
    assert settings.api_prefix == "/api/v1"
    assert settings.sync_max_timeout_s == 120
    assert settings.outbox_publisher_enabled is True


def test_env_override(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@db:5432/x")
    monkeypatch.setenv("SYNC_MAX_TIMEOUT_S", "42")
    monkeypatch.setenv("LOG_JSON", "false")

    settings = Settings()

    assert settings.database_url == "postgresql+asyncpg://u:p@db:5432/x"
    assert settings.sync_max_timeout_s == 42
    assert settings.log_json is False


def test_log_dir_is_unset_by_default():
    settings = Settings()

    assert settings.log_dir is None
    assert settings.log_max_bytes == 10 * 1024 * 1024
    assert settings.log_backup_count == 5


def test_blank_log_dir_is_unset(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LOG_DIR", "   ")

    assert Settings().log_dir is None


def test_log_dir_from_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LOG_DIR", "logs")
    monkeypatch.setenv("LOG_MAX_BYTES", "1024")
    monkeypatch.setenv("LOG_BACKUP_COUNT", "2")

    settings = Settings()

    assert settings.log_dir == "logs"
    assert settings.log_max_bytes == 1024
    assert settings.log_backup_count == 2


def test_get_settings_is_cached(monkeypatch: pytest.MonkeyPatch):
    first = get_settings()
    second = get_settings()
    assert first is second

    monkeypatch.setenv("SYNC_MAX_TIMEOUT_S", "7")
    reset_settings_cache()
    assert get_settings().sync_max_timeout_s == 7


def test_prod_requires_jwt_secret(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ENVIRONMENT", "prod")
    with pytest.raises(ValidationError) as excinfo:
        Settings()

    message = str(excinfo.value)
    assert "JWT_SECRET" in message
    assert "SECRET_ENC_KEY" not in message


def test_prod_requires_enc_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ENVIRONMENT", "prod")
    monkeypatch.setenv("JWT_SECRET", "a-real-secret")
    with pytest.raises(ValidationError) as excinfo:
        Settings()

    message = str(excinfo.value)
    assert "SECRET_ENC_KEY" in message
    assert DEV_JWT_SECRET not in message


def test_prod_accepts_explicit_secrets(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ENVIRONMENT", "prod")
    monkeypatch.setenv("JWT_SECRET", "a-real-secret")
    monkeypatch.setenv("SECRET_ENC_KEY", "another-real-encryption-key")

    settings = Settings()

    assert settings.environment == "prod"
    assert settings.jwt_secret != DEV_JWT_SECRET
    assert settings.secret_enc_key != DEV_ENC_KEY
