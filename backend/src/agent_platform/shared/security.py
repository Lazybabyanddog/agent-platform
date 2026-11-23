"""密码哈希、JWT、API Key 与对称加密。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from agent_platform.settings import get_settings
from agent_platform.shared.errors import UnauthorizedError

_hasher = PasswordHasher()
_CIPHER_PREFIX = "v1:"


# ---------------------------------------------------------------- 密码


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, VerifyMismatchError, InvalidHashError):
        return False


# ---------------------------------------------------------------- JWT


def create_access_token(
    *,
    actor_id: uuid.UUID,
    tenant_id: uuid.UUID,
    actor_type: str = "USER",
    ttl_seconds: int | None = None,
) -> tuple[str, int]:
    settings = get_settings()
    ttl = ttl_seconds or settings.jwt_ttl_seconds
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(actor_id),
        "tid": str(tenant_id),
        "typ": actor_type,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl)).timestamp()),
        "jti": uuid.uuid4().hex,
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, ttl


def decode_access_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.ExpiredSignatureError as exc:
        raise UnauthorizedError("token expired") from exc
    except jwt.InvalidTokenError as exc:
        raise UnauthorizedError("invalid token") from exc


# ---------------------------------------------------------------- API Key


def generate_api_key(prefix: str | None = None) -> tuple[str, str, str]:
    """返回 (key_id, secret, 完整明文)。明文只在创建响应中出现一次。"""
    prefix = prefix or get_settings().api_key_prefix
    key_id = secrets.token_hex(12)
    secret = secrets.token_urlsafe(32)
    return key_id, secret, f"{prefix}_{key_id}_{secret}"


def hash_api_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def parse_api_key(plaintext: str, prefix: str | None = None) -> tuple[str, str] | None:
    prefix = prefix or get_settings().api_key_prefix
    parts = plaintext.split("_", 2)
    if len(parts) != 3 or parts[0] != prefix or not parts[1] or not parts[2]:
        return None
    return parts[1], parts[2]


def verify_api_secret(stored_hash: str, secret: str) -> bool:
    return hmac.compare_digest(stored_hash, hash_api_secret(secret))


# ---------------------------------------------------------------- 凭据加解密


def _cipher() -> AESGCM:
    key = hashlib.sha256(get_settings().secret_enc_key.encode("utf-8")).digest()
    return AESGCM(key)


def encrypt_secret(plaintext: str) -> str:
    nonce = os.urandom(12)
    ciphertext = _cipher().encrypt(nonce, plaintext.encode("utf-8"), None)
    return _CIPHER_PREFIX + base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")


def decrypt_secret(token: str) -> str:
    if not token.startswith(_CIPHER_PREFIX):
        raise ValueError("unsupported ciphertext format")
    raw = base64.urlsafe_b64decode(token[len(_CIPHER_PREFIX) :])
    nonce, ciphertext = raw[:12], raw[12:]
    return _cipher().decrypt(nonce, ciphertext, None).decode("utf-8")


def mask_secret(value: str, *, keep: int = 4) -> str:
    if len(value) <= keep * 2:
        return "*" * len(value)
    return f"{value[:keep]}{'*' * 6}{value[-keep:]}"
