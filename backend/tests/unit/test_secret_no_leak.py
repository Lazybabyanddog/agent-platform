"""凭据加密层：明文不入库、不出现在可展示字段里。"""

from __future__ import annotations

from agent_platform.shared.security import decrypt_secret, encrypt_secret, mask_secret


def test_ciphertext_does_not_contain_plaintext() -> None:
    plaintext = "sk-live-0123456789abcdef"
    ciphertext = encrypt_secret(plaintext)
    assert ciphertext.startswith("v1:")
    assert plaintext not in ciphertext
    assert decrypt_secret(ciphertext) == plaintext


def test_encryption_is_randomized() -> None:
    plaintext = "same-input"
    assert encrypt_secret(plaintext) != encrypt_secret(plaintext)


def test_mask_hides_middle() -> None:
    masked = mask_secret("sk-live-0123456789abcdef")
    assert masked.startswith("sk-l")
    assert masked.endswith("cdef")
    assert "0123456789ab" not in masked


def test_mask_fully_hides_short_values() -> None:
    assert set(mask_secret("short")) == {"*"}
