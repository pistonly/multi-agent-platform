"""Fernet-based at-rest encryption for sensitive string columns (cleanup follow-up PR3).

Used by ``server.domain.encrypted_types.EncryptedString`` to make
``Webhook.secret`` (and future sensitive columns) opaque in DB dumps —
the column at rest is a Fernet token; the application decrypts on read.

Key resolution
--------------
The Fernet key (32-byte urlsafe-base64) is loaded from
``MAP_WEBHOOK_SECRET_ENCRYPTION_KEY``. If unset, ``get_fernet()`` raises
``SecretEncryptionKeyMissing`` — we deliberately do NOT silently derive a
key from another secret (e.g. ``MAP_ADMIN_TOKEN``), because that would
make "key rotation by env var change" ambiguous.

Migration coupling
------------------
Alembic migration 037 calls :func:`encrypt_value` directly to backfill
existing plaintext ``Webhook.secret`` rows. The migration loads the same
env var via ``server.config.get_settings`` so prod / dev / test share one
key path.

Test override
-------------
Tests can call :func:`set_key_for_tests` to inject a deterministic key
without touching env vars / settings cache.
"""

from __future__ import annotations

import threading

from cryptography.fernet import Fernet

from server.config import get_settings

_TEST_KEY_LOCK = threading.Lock()
_TEST_KEY: bytes | None = None


class SecretEncryptionKeyMissing(RuntimeError):
    """Raised when ``MAP_WEBHOOK_SECRET_ENCRYPTION_KEY`` is unset and no
    test key has been injected via :func:`set_key_for_tests`.

    This is intentional: we never silently fall back to deriving a key
    from another secret, because that would make key rotation
    ambiguous.
    """


def _load_key_from_settings() -> bytes:
    raw = get_settings().webhook_secret_encryption_key
    if not raw:
        raise SecretEncryptionKeyMissing(
            "MAP_WEBHOOK_SECRET_ENCRYPTION_KEY is not set. "
            "Generate one with `python -c 'from cryptography.fernet import "
            "Fernet; print(Fernet.generate_key().decode())'` and add it "
            "to your environment / .env file."
        )
    return raw.encode("utf-8")


def get_fernet() -> Fernet:
    """Return a :class:`cryptography.fernet.Fernet` keyed for the current process."""
    if _TEST_KEY is not None:
        return Fernet(_TEST_KEY)
    return Fernet(_load_key_from_settings())


def set_key_for_tests(key: bytes | None) -> None:
    """Inject a deterministic key for the current test. Pass ``None`` to clear.

    Only intended for unit tests; production code paths should resolve the
    key via env var.
    """
    with _TEST_KEY_LOCK:
        global _TEST_KEY
        _TEST_KEY = key


def encrypt_value(plaintext: str) -> str:
    """Encrypt ``plaintext`` and return the Fernet token as a UTF-8 string."""
    if plaintext is None:
        raise ValueError("encrypt_value requires a non-None plaintext")
    return get_fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_value(ciphertext: str) -> str:
    """Decrypt a Fernet token back to its plaintext UTF-8 string.

    Raises ``InvalidToken`` if the ciphertext was encrypted with a different
    key, or if it isn't a valid Fernet token. Callers (TypeDecorator,
    migration) must NOT swallow this — silent decryption failures would
    send an empty / garbage HMAC secret to webhook receivers.
    """
    if ciphertext is None:
        raise ValueError("decrypt_value requires a non-None ciphertext")
    return get_fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")
