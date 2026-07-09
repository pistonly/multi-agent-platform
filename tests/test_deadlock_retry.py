"""Unit tests for ``server.db.deadlock_retry``.

Coverage
--------
* ``is_deadlock`` recognises PG SQLSTATE ``40P01`` / ``40001`` via
  ``orig.pgcode`` and falls back to message string scan (SQLite tests).
* ``commit_with_retry`` retries on the first N attempts then succeeds.
* ``commit_with_retry`` raises the last exception when retries exhausted.
* Disabled via env (``MAP_DEADLOCK_RETRY_ENABLED=0``) → no retry, raise
  immediately.
* Non-deadlock ``OperationalError`` is re-raised without retry.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from sqlalchemy.exc import IntegrityError, OperationalError

from server.db import deadlock_retry


class _FakeOrig:
    def __init__(self, pgcode: str | None = None, msg: str = "") -> None:
        self.pgcode = pgcode
        self.msg = msg

    def __str__(self) -> str:
        return self.msg


def _pg_deadlock() -> OperationalError:
    return OperationalError("stmt", {}, _FakeOrig(pgcode="40P01", msg="deadlock detected"))


def _pg_serialization_failure() -> OperationalError:
    return OperationalError("stmt", {}, _FakeOrig(pgcode="40001", msg="could not serialize"))


def _pg_unique_violation() -> OperationalError:
    """Not a deadlock — should not be retried."""
    return OperationalError("stmt", {}, _FakeOrig(pgcode="23505", msg="duplicate key"))


def _sqlite_locked() -> OperationalError:
    """SQLite OperationalError — no pgcode; message has 'database is locked'.

    We intentionally do *not* treat this as a deadlock (string scan
    matches "deadlock" / "serialization failure" only).
    """
    return OperationalError("stmt", {}, _FakeOrig(msg="database is locked"))


# ---------------------------------------------------------------------------
# is_deadlock
# ---------------------------------------------------------------------------


def test_is_deadlock_pg_deadlock_detected():
    assert deadlock_retry.is_deadlock(_pg_deadlock()) is True


def test_is_deadlock_pg_serialization_failure():
    assert deadlock_retry.is_deadlock(_pg_serialization_failure()) is True


def test_is_deadlock_unique_violation_is_not_deadlock():
    assert deadlock_retry.is_deadlock(_pg_unique_violation()) is False


def test_is_deadlock_sqlite_locked_is_not_deadlock():
    """SQLite 'database is locked' is a separate code path; don't retry here."""
    assert deadlock_retry.is_deadlock(_sqlite_locked()) is False


def test_is_deadlock_non_operational_error():
    assert deadlock_retry.is_deadlock(ValueError("nope")) is False


# ---------------------------------------------------------------------------
# commit_with_retry
# ---------------------------------------------------------------------------


class _FakeSession:
    """Minimal session stub: tracks commit / rollback call counts."""

    def __init__(self, side_effects: list[Exception | None]) -> None:
        self._side_effects = list(side_effects)
        self.commits = 0
        self.rollbacks = 0

    def commit(self) -> None:
        self.commits += 1
        if self._side_effects:
            effect = self._side_effects.pop(0)
            if effect is not None:
                raise effect

    def rollback(self) -> None:
        self.rollbacks += 1


def test_commit_with_retry_succeeds_first_try():
    sess = _FakeSession([None])
    attempts = deadlock_retry.commit_with_retry(
        sess, max_retries=3, base_delay_ms=1, op_label="t"
    )
    assert attempts == 1
    assert sess.commits == 1
    assert sess.rollbacks == 0


def test_commit_with_retry_succeeds_after_two_deadlocks(monkeypatch):
    monkeypatch.setattr(deadlock_retry, "_enabled", lambda: True)
    sess = _FakeSession([_pg_deadlock(), _pg_serialization_failure(), None])
    attempts = deadlock_retry.commit_with_retry(
        sess, max_retries=3, base_delay_ms=1, op_label="t"
    )
    assert attempts == 3
    assert sess.commits == 3
    assert sess.rollbacks == 2


def test_commit_with_retry_exhausts_and_raises(monkeypatch):
    monkeypatch.setattr(deadlock_retry, "_enabled", lambda: True)
    sess = _FakeSession([_pg_deadlock()] * 5)
    with pytest.raises(OperationalError):
        deadlock_retry.commit_with_retry(
            sess, max_retries=3, base_delay_ms=1, op_label="t"
        )
    # 1 initial + 3 retries = 4 commits before the final raise on attempt 5.
    assert sess.commits == 4
    assert sess.rollbacks == 3


def test_commit_with_retry_disabled_skips_retry(monkeypatch):
    monkeypatch.setattr(deadlock_retry, "_enabled", lambda: False)
    sess = _FakeSession([_pg_deadlock(), None])
    with pytest.raises(OperationalError):
        deadlock_retry.commit_with_retry(
            sess, max_retries=3, base_delay_ms=1, op_label="t"
        )
    assert sess.commits == 1
    assert sess.rollbacks == 0


def test_commit_with_retry_non_deadlock_passes_through(monkeypatch):
    monkeypatch.setattr(deadlock_retry, "_enabled", lambda: True)
    sess = _FakeSession([_pg_unique_violation()])
    with pytest.raises(OperationalError):
        deadlock_retry.commit_with_retry(
            sess, max_retries=3, base_delay_ms=1, op_label="t"
        )
    assert sess.commits == 1
    assert sess.rollbacks == 0


def test_commit_with_retry_uses_exponential_backoff(monkeypatch):
    monkeypatch.setattr(deadlock_retry, "_enabled", lambda: True)
    sleeps: list[float] = []

    def fake_sleep(s: float) -> None:
        sleeps.append(s)

    monkeypatch.setattr(deadlock_retry.time, "sleep", fake_sleep)

    sess = _FakeSession([_pg_deadlock(), _pg_deadlock(), None])
    deadlock_retry.commit_with_retry(
        sess, max_retries=3, base_delay_ms=100, op_label="t"
    )
    # 100ms / 200ms (third attempt succeeds with no sleep)
    assert sleeps == pytest.approx([0.1, 0.2])


def test_commit_with_retry_env_override(monkeypatch):
    monkeypatch.setenv("MAP_DEADLOCK_RETRY_ENABLED", "0")
    sess = _FakeSession([None])
    attempts = deadlock_retry.commit_with_retry(
        sess, max_retries=3, base_delay_ms=1, op_label="t"
    )
    assert attempts == 1
    assert sess.commits == 1
