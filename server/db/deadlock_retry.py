"""Deadlock retry helper (race experiment eca0f522 PR1).

SQLAlchemy event-driven retry on PG ``deadlock_detected`` (SQLSTATE
``40P01``) and ``serialization_failure`` (``40001``). Exponential backoff
``base_delay_ms * 2**attempt`` capped at ``max_retries`` (default
``100ms / 200ms / 400ms``, 3 attempts).

Why a helper instead of a SQLAlchemy event listener
---------------------------------------------------
SQLAlchemy's ``engine`` event API fires for low-level connection /
checkout events, not for ``session.commit()`` outcomes. The cleanest
hook point is the caller, so we expose ``commit_with_retry(db)`` for
hot paths (``lock_service.acquire_experiment_lock``, ``notification_
service._upsert_notification``) and keep the retry policy here.

Config (env)
------------
* ``MAP_DEADLOCK_RETRY_ENABLED``  default ``"1"`` (set ``"0"`` to disable)
* ``MAP_DEADLOCK_RETRY_MAX``      default ``"3"``
* ``MAP_DEADLOCK_RETRY_BASE_MS``  default ``"100"``

Audit
-----
Retry attempts are logged via ``map.deadlock_retry``; the waker's
existing ``inbound_event`` audit log captures the final outcome so the
retry count is observable per-experiment via log inspection rather than
a dedicated audit row (avoids audit table bloat).
"""

from __future__ import annotations

import logging
import os
import time

from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.orm import Session

logger = logging.getLogger("map.deadlock_retry")

_DEFAULT_MAX_RETRIES = 3
_DEFAULT_BASE_MS = 100

# PG SQLSTATE codes we retry on.
_RETRY_SQLSTATES = frozenset({"40P01", "40001"})


def _enabled() -> bool:
    return os.environ.get("MAP_DEADLOCK_RETRY_ENABLED", "1") != "0"


def _max_retries() -> int:
    raw = os.environ.get("MAP_DEADLOCK_RETRY_MAX", str(_DEFAULT_MAX_RETRIES))
    try:
        return max(0, int(raw))
    except ValueError:
        return _DEFAULT_MAX_RETRIES


def _base_delay_ms() -> int:
    raw = os.environ.get("MAP_DEADLOCK_RETRY_BASE_MS", str(_DEFAULT_BASE_MS))
    try:
        return max(0, int(raw))
    except ValueError:
        return _DEFAULT_BASE_MS


def is_deadlock(exc: BaseException) -> bool:
    """True if ``exc`` is a PG deadlock_detected / serialization_failure.

    Falls back to a string scan on the message so the same predicate
    works for SQLite (which raises ``OperationalError`` with
    ``"database is locked"`` — *not* retried here; that's a different
    code path in :mod:`server.db.session`).
    """
    if not isinstance(exc, OperationalError):
        return False
    orig = getattr(exc, "orig", None)
    pgcode = getattr(orig, "pgcode", None)
    if pgcode in _RETRY_SQLSTATES:
        return True
    sqlstate = getattr(orig, "sqlstate", None)
    if callable(sqlstate):
        sqlstate = sqlstate()
    if sqlstate in _RETRY_SQLSTATES:
        return True
    msg = str(orig or exc).lower()
    return "deadlock" in msg or "serialization failure" in msg


def commit_with_retry(
    db: Session,
    *,
    max_retries: int | None = None,
    base_delay_ms: int | None = None,
    op_label: str = "commit",
) -> int:
    """Wrap ``db.commit()`` with deadlock retry.

    Returns the attempt count (1 = first try succeeded). Raises the last
    deadlock ``OperationalError`` if all retries are exhausted.
    """
    if not _enabled():
        db.commit()
        return 1

    effective_max = max_retries if max_retries is not None else _max_retries()
    effective_base = base_delay_ms if base_delay_ms is not None else _base_delay_ms()
    last_exc: DBAPIError | None = None

    for attempt in range(effective_max + 1):
        try:
            db.commit()
            if attempt:
                logger.info(
                    "deadlock_retry op=%s attempt=%s succeeded",
                    op_label,
                    attempt + 1,
                )
            return attempt + 1
        except DBAPIError as exc:
            last_exc = exc
            if not is_deadlock(exc) or attempt >= effective_max:
                raise
            delay_s = (effective_base / 1000.0) * (2 ** attempt)
            logger.warning(
                "deadlock_retry op=%s attempt=%s/%s pgcode=%s retry_in_ms=%s",
                op_label,
                attempt + 1,
                effective_max,
                getattr(getattr(exc, "orig", None), "pgcode", None),
                int(delay_s * 1000),
            )
            try:
                db.rollback()
            except Exception:  # pragma: no cover - defensive
                logger.exception("deadlock_retry rollback failed op=%s", op_label)
            time.sleep(delay_s)

    # Unreachable: the loop either returns or raises on the final attempt.
    if last_exc is not None:
        raise last_exc
    return effective_max + 1


__all__ = ["commit_with_retry", "is_deadlock"]
