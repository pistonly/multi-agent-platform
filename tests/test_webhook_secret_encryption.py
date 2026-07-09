"""At-rest encryption for ``Webhook.secret`` (cleanup follow-up PR3).

``server.domain.models.Webhook.secret`` is wrapped in
``EncryptedString`` so the DB stores a Fernet token, not the raw HMAC
signing secret. These tests pin down that contract:

* Writing a plaintext secret via the ORM encrypts on flush.
* Reading the same row back via the ORM decrypts to the original plaintext.
* The HMAC signing path (``webhook_service._perform_delivery``) still
  gets the plaintext secret via ``webhook.secret``, so existing delivery
  semantics are preserved.
* A wrong key (e.g. key rotated, test injects the wrong one) raises
  ``InvalidToken`` at read time — we deliberately do NOT silently fall
  back to the raw column value (would produce garbage HMAC signatures).
* Migration 037: starting from a plaintext row, the backfill produces
  a ciphertext row that the TypeDecorator decrypts back to the original
  plaintext.

All tests inject a deterministic Fernet key via
``server.services.secret_encryption.set_key_for_tests`` so they don't
need ``MAP_WEBHOOK_SECRET_ENCRYPTION_KEY`` in the test environment.
"""

from __future__ import annotations

import secrets
import uuid

import pytest
from cryptography.fernet import Fernet, InvalidToken
from map_types.enums import AgentRole
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from server.domain.models import Agent, Webhook
from server.services.secret_encryption import (
    SecretEncryptionKeyMissing,
    decrypt_value,
    encrypt_value,
    set_key_for_tests,
)

KEY_A = Fernet.generate_key()
KEY_B = Fernet.generate_key()
PLAINTEXT = "hmac-signing-secret-" + secrets.token_urlsafe(24)


@pytest.fixture(autouse=True)
def _fernet_key():
    """Inject a deterministic Fernet key for every test in this module."""
    set_key_for_tests(KEY_A)
    yield
    set_key_for_tests(None)


def _make_agent(db: Session, *, project_id: uuid.UUID, name: str) -> Agent:
    agent = Agent(
        id=uuid.uuid4(),
        project_id=project_id,
        name=name,
        api_token_hash="test-hash",
        api_token_prefix="test",
        role=AgentRole.agent,
    )
    db.add(agent)
    db.flush()
    return agent


def _make_webhook(db: Session, *, project_id: uuid.UUID, secret: str) -> Webhook:
    wh = Webhook(
        id=uuid.uuid4(),
        project_id=project_id,
        url="https://example.invalid/hook",
        events=["experiment.phase_changed"],
        secret=secret,
        active=True,
    )
    db.add(wh)
    db.flush()
    return wh


def test_encrypt_decrypt_roundtrip() -> None:
    ciphertext = encrypt_value(PLAINTEXT)
    assert ciphertext != PLAINTEXT
    assert ciphertext.startswith("gAAAAA")  # Fernet token marker
    assert decrypt_value(ciphertext) == PLAINTEXT


def test_decrypt_with_wrong_key_raises() -> None:
    ciphertext = encrypt_value(PLAINTEXT)
    set_key_for_tests(KEY_B)
    with pytest.raises(InvalidToken):
        decrypt_value(ciphertext)


def test_decrypt_value_rejects_none() -> None:
    with pytest.raises(ValueError, match="requires a non-None"):
        decrypt_value(None)  # type: ignore[arg-type]


def test_encrypt_value_rejects_none() -> None:
    with pytest.raises(ValueError, match="requires a non-None"):
        encrypt_value(None)  # type: ignore[arg-type]


def test_orm_write_stores_ciphertext(db_session, project) -> None:
    """``Webhook.secret`` on disk is Fernet ciphertext, not plaintext."""
    project_id = uuid.UUID(project["id"])
    wh = _make_webhook(db_session, project_id=project_id, secret=PLAINTEXT)
    db_session.commit()

    # Read the raw column value via SQL (bypass ORM TypeDecorator).
    # SQLite stores UUID as 32-char hex (no hyphens), so compare against
    # ``wh.id.hex`` rather than ``str(wh.id)``.
    raw = db_session.execute(
        text("SELECT secret FROM webhooks WHERE id = :id"),
        {"id": wh.id.hex},
    ).scalar_one()
    assert raw != PLAINTEXT
    assert raw.startswith("gAAAAA")
    # And the plaintext is not literally in the ciphertext.
    assert PLAINTEXT not in raw


def test_orm_read_decrypts_to_plaintext(db_session, project) -> None:
    project_id = uuid.UUID(project["id"])
    wh = _make_webhook(db_session, project_id=project_id, secret=PLAINTEXT)
    db_session.commit()
    db_session.expire_all()

    fetched = db_session.scalar(select(Webhook).where(Webhook.id == wh.id))
    assert fetched is not None
    assert fetched.secret == PLAINTEXT


def test_hmac_signing_still_uses_plaintext_secret(db_session, project, monkeypatch) -> None:
    """Round-trip: ``webhook.secret`` from ORM is the plaintext secret,
    so the existing ``hmac.new(webhook.secret.encode(...), ...)`` call in
    ``webhook_service._perform_delivery`` keeps working.
    """
    import hashlib
    import hmac

    project_id = uuid.UUID(project["id"])
    wh = _make_webhook(db_session, project_id=project_id, secret=PLAINTEXT)
    db_session.commit()
    db_session.expire_all()

    fetched = db_session.scalar(select(Webhook).where(Webhook.id == wh.id))
    assert fetched is not None
    body = b'{"event":"experiment.phase_changed"}'
    sig = "sha256=" + hmac.new(fetched.secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    assert sig.startswith("sha256=")
    # And it matches what we'd compute from the plaintext directly.
    expected = "sha256=" + hmac.new(PLAINTEXT.encode("utf-8"), body, hashlib.sha256).hexdigest()
    assert sig == expected


def test_wrong_key_on_read_raises(db_session, project) -> None:
    """Defense-in-depth: if the Fernet key is rotated and a stale row is
    read, decryption must fail loudly rather than silently return garbage
    that would corrupt HMAC signatures.
    """
    project_id = uuid.UUID(project["id"])
    wh = _make_webhook(db_session, project_id=project_id, secret=PLAINTEXT)
    db_session.commit()
    db_session.expire_all()

    # Rotate the key (simulate env var change / migration to new key).
    set_key_for_tests(KEY_B)
    db_session.expire_all()

    with pytest.raises(InvalidToken):
        db_session.scalar(select(Webhook).where(Webhook.id == wh.id))


def test_missing_env_var_raises(monkeypatch) -> None:
    """When neither an env var nor a test key is set, encrypt fails fast."""
    set_key_for_tests(None)
    monkeypatch.delenv("MAP_WEBHOOK_SECRET_ENCRYPTION_KEY", raising=False)
    from server.config import get_settings

    get_settings.cache_clear()
    try:
        with pytest.raises(SecretEncryptionKeyMissing, match="MAP_WEBHOOK_SECRET_ENCRYPTION_KEY"):
            encrypt_value("anything")
    finally:
        get_settings.cache_clear()


def test_migration_backfill_encrypts_in_place(db_session, project) -> None:
    """Simulate migration 037: starting from a plaintext row, encrypt via
    the helper, write back. Subsequent ORM read decrypts correctly.
    """
    project_id = uuid.UUID(project["id"])
    # Insert a plaintext row directly via SQL (simulating pre-migration state).
    wh_id = uuid.uuid4()
    db_session.execute(
        text(
            "INSERT INTO webhooks (id, project_id, url, events, secret, active) "
            "VALUES (:id, :pid, 'https://example.invalid/m', '[]', :plaintext, 1)"
        ),
        {"id": wh_id.hex, "pid": project_id.hex, "plaintext": PLAINTEXT},
    )
    db_session.commit()

    # Backfill: encrypt and write back.
    db_session.execute(
        text("UPDATE webhooks SET secret = :cipher WHERE id = :id"),
        {"cipher": encrypt_value(PLAINTEXT), "id": wh_id.hex},
    )
    db_session.commit()

    # Raw column is now ciphertext.
    raw = db_session.execute(
        text("SELECT secret FROM webhooks WHERE id = :id"), {"id": wh_id.hex}
    ).scalar_one()
    assert raw != PLAINTEXT
    assert raw.startswith("gAAAAA")

    # ORM read returns plaintext.
    db_session.expire_all()
    fetched = db_session.scalar(select(Webhook).where(Webhook.id == wh_id))
    assert fetched is not None
    assert fetched.secret == PLAINTEXT
