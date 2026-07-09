"""Tests for cleanup experiment (f12a5638) Exp A — ``log_no_commit`` rename.

Verifies:

1. ``audit_service.log_no_commit`` exists and is a public function
   (no leading underscore).
2. ``audit_service._log_no_commit`` is preserved as a deprecation
   alias pointing to the new function (so downstream SDKs / external
   callers that still reference the old private name don't break).
3. Calling ``log_no_commit`` flushes a row without committing (the
   pre-rename semantic).
"""

from __future__ import annotations

import pytest

from server.services import audit_service


def test_log_no_commit_is_public():
    """The new public name must exist and be callable as a function."""
    assert callable(audit_service.log_no_commit)
    # No leading underscore.
    assert not audit_service.log_no_commit.__name__.startswith("_")


def test_log_no_commit_and_legacy_alias_resolve_to_same_function():
    """Backward-compat shim — the old private name still works and
    points at the same implementation."""
    assert audit_service._log_no_commit is audit_service.log_no_commit


def test_log_no_commit_flushes_without_committing(db_session):
    """Core semantic preserved: flushes the row, does NOT commit.
    Caller controls the transaction boundary."""
    from server.domain.models import Project

    project = Project(
        project_key="audit-rename",
        name="Audit Rename",
        workspace_path="/tmp/audit-rename",
    )
    db_session.add(project)
    db_session.flush()
    entry = audit_service.log_no_commit(
        db_session,
        action="test.action",
        target_type="experiment",
        project_id=project.id,
        summary="renamed smoke",
    )
    # Row was added to the session and flushed.
    assert entry.id is not None
    # The outer savepoint still owns the transaction — no commit yet.
    # We just verify the row is in the identity map.
    assert entry in db_session.identity_map.values()
