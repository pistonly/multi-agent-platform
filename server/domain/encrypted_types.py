"""SQLAlchemy TypeDecorators for at-rest encryption of sensitive columns.

Used by ``server.domain.models.Webhook.secret`` (cleanup follow-up PR3).
The on-disk representation is a Fernet token (opaque to DBAs / anyone
with ``SELECT secret FROM webhooks``); the Python-side attribute is the
plaintext secret, so existing application code keeps working unchanged.

Usage
-----
.. code-block:: python

    from server.domain.encrypted_types import EncryptedString

    class Webhook(Base):
        ...
        secret: Mapped[str] = mapped_column(EncryptedString(255), nullable=False)

After this, ``webhook.secret = "plain"`` encrypts on flush; reading
``webhook.secret`` decrypts on access.

Failure modes
-------------
- Decryption failure (``InvalidToken``) propagates as-is. We deliberately
  do NOT silently fall back to returning the raw column value — a
  garbage HMAC secret would silently break webhook delivery.
- The key is resolved at call time via
  :func:`server.services.secret_encryption.get_fernet`. Tests inject a
  deterministic key via :func:`set_key_for_tests`.
"""

from __future__ import annotations

from sqlalchemy.types import String, TypeDecorator

from server.services.secret_encryption import decrypt_value, encrypt_value


class EncryptedString(TypeDecorator):
    """Transparent Fernet encryption wrapper around ``VARCHAR(N)``.

    Stores ciphertext in the DB; exposes plaintext on the Python side.
    Pass the desired column length (e.g. ``EncryptedString(255)``) to
    keep parity with the underlying ``String`` column — Fernet tokens
    are ~100 bytes for short plaintexts but grow with input, so leave
    generous headroom.
    """

    impl = String
    cache_ok = True

    def __init__(self, length: int) -> None:
        super().__init__()
        self.impl_instance = String(length)

    def process_bind_param(self, value: str | None, dialect: object) -> str | None:
        if value is None:
            return None
        return encrypt_value(value)

    def process_result_value(self, value: str | None, dialect: object) -> str | None:
        if value is None:
            return None
        return decrypt_value(value)
