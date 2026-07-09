"""webhooks.secret Fernet encryption (cleanup follow-up c9281d86 PR3).

Two changes rolled into one migration (kept together because widening
the column without immediately backfilling encrypted values would leave
rows that the application can't decrypt):

1. **Column widening**: ``webhooks.secret`` goes from ``VARCHAR(255)`` to
   ``VARCHAR(1024)``. Fernet tokens for typical 32-byte HMAC signing
   secrets are ~96 chars; 1024 leaves generous headroom for rotation /
   future format changes. Existing rows are NOT truncated — the column
   only grows.

2. **At-rest encryption backfill**: every existing row's plaintext
   ``secret`` is encrypted via
   :func:`server.services.secret_encryption.encrypt_value` (Fernet
   symmetric encryption under ``MAP_WEBHOOK_SECRET_ENCRYPTION_KEY``) and
   the ciphertext replaces the plaintext in-place. After this migration,
   the application's ``EncryptedString`` TypeDecorator transparently
   decrypts on read.

Why no DB-level CHECK / new column
----------------------------------
We could have introduced ``secret_cipher VARCHAR(1024)`` alongside the
plain ``secret`` and migrated reads over a deprecation window. The
trade-off: two columns of secret material per row during the window,
plus a fallback path that silently returns plaintext on read (security
regression if anyone forgets to rotate). Single-column with Fernet is
simpler and the rotation path is "regenerate webhook secret" — no
coexistence period.

Failure modes
-------------
- ``MAP_WEBHOOK_SECRET_ENCRYPTION_KEY`` unset → migration aborts with
  ``SecretEncryptionKeyMissing`` (no partial writes).
- Wrong key (e.g. previous prod key lost) → application reads will
  raise ``InvalidToken`` for existing webhooks; recovery is to
  regenerate the secret via the API (``POST /webhooks/{id}/rotate`` is
  not in scope here — manual UPDATE via admin).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "037"
down_revision: str | None = "036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_column(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def _has_table(inspector: sa.Inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _has_table(inspector, "webhooks"):
        return
    if not _has_column(inspector, "webhooks", "secret"):
        return

    # Late import: alembic env may not have the cryptography / app deps
    # loaded for slim migration containers.
    from server.services.secret_encryption import encrypt_value

    # 1. Widen the column. Idempotent: if already VARCHAR(1024) the
    #    alter is a no-op on PG (same type) and a no-op on SQLite (no
    #    strict typing).
    with op.batch_alter_table("webhooks") as batch_op:
        batch_op.alter_column(
            "secret",
            existing_type=sa.String(length=255),
            type_=sa.String(length=1024),
            existing_nullable=False,
        )

    # 2. Encrypt existing plaintext rows in-place. The application's
    #    TypeDecorator will decrypt on read from this point forward.
    rows = bind.execute(sa.text("SELECT id, secret FROM webhooks")).all()
    for row_id, plaintext in rows:
        if plaintext is None:
            continue
        # Skip rows that are already Fernet tokens (idempotent guard so
        # re-running the migration against a partially-upgraded DB is
        # safe).
        if plaintext.startswith("gAAAAA"):
            continue
        bind.execute(
            sa.text("UPDATE webhooks SET secret = :cipher WHERE id = :id"),
            {"cipher": encrypt_value(plaintext), "id": row_id},
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _has_table(inspector, "webhooks"):
        return
    if not _has_column(inspector, "webhooks", "secret"):
        return

    from server.services.secret_encryption import decrypt_value

    # Decrypt rows back to plaintext BEFORE shrinking the column —
    # otherwise a Fernet token that doesn't fit VARCHAR(255) would be
    # silently truncated.
    rows = bind.execute(sa.text("SELECT id, secret FROM webhooks")).all()
    for row_id, ciphertext in rows:
        if ciphertext is None:
            continue
        if not ciphertext.startswith("gAAAAA"):
            continue  # already plaintext (or unrecognized — leave alone)
        bind.execute(
            sa.text("UPDATE webhooks SET secret = :plain WHERE id = :id"),
            {"plain": decrypt_value(ciphertext), "id": row_id},
        )

    with op.batch_alter_table("webhooks") as batch_op:
        batch_op.alter_column(
            "secret",
            existing_type=sa.String(length=1024),
            type_=sa.String(length=255),
            existing_nullable=False,
        )
