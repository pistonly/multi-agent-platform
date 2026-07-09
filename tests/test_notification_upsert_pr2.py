"""Unit tests for race experiment (eca0f522) PR2 atomic upsert.

Covers the post-PR2 behaviour of
``server.services.notification_service._upsert_notification``:

* fresh insert (no matching row) — single row, ``event_count=1``,
  ``wake_version=1``, ``read_at=None``
* merge into existing row — same row id, ``event_count`` monotonic,
  ``wake_version`` bumps only for ``wakeable`` category
* digest merge — ``wake_version`` unchanged (fingerprint stability)
* read_at reset on every merge
* fingerprint_version preserved on merge (legacy v1 stays v1)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from server.domain.models import Agent, AgentRole, Notification, NotificationFingerprintVersion, Project
from server.services import notification_service
from server.services.auth import create_agent


def _project(db_session) -> Project:
    project = Project(
        project_key="notif-upsert",
        name="Notif Upsert",
        workspace_path="/tmp/notif-upsert",
    )
    db_session.add(project)
    db_session.flush()
    return project


def _agent(db_session, project: Project, name: str = "host") -> Agent:
    agent, _ = create_agent(db_session, name, AgentRole.agent, project_id=project.id)
    return agent


def _upsert(db_session, recipient, *, category):
    return notification_service._upsert_notification(
        db_session,
        recipient_agent_id=recipient.id,
        project_id=None,
        event="experiment.lock.no_progress",
        summary="locked",
        target_type="experiment",
        target_id=uuid.uuid4(),
        payload={"experiment_id": "e1"},
        category=category,
    )


def test_upsert_creates_row_when_no_match(db_session):
    project = _project(db_session)
    recipient = _agent(db_session, project, "host")
    row = _upsert(db_session, recipient, category=notification_service.NotificationCategory.wakeable)
    assert row.id is not None
    assert row.event_count == 1
    assert row.wake_version == 1
    assert row.read_at is None
    assert row.fingerprint_version == NotificationFingerprintVersion.v2


def test_upsert_merges_existing_row_and_bumps_count(db_session):
    project = _project(db_session)
    recipient = _agent(db_session, project, "host")
    target_id = uuid.uuid4()
    common = dict(
        recipient_agent_id=recipient.id,
        project_id=None,
        event="experiment.lock.no_progress",
        summary="locked",
        target_type="experiment",
        target_id=target_id,
        payload={"experiment_id": "e1"},
    )

    first = notification_service._upsert_notification(
        db_session, category=notification_service.NotificationCategory.wakeable, **common
    )
    db_session.commit()
    first_id = first.id
    first_wake = first.wake_version

    second = notification_service._upsert_notification(
        db_session, category=notification_service.NotificationCategory.wakeable, **common
    )
    db_session.commit()
    assert second.id == first_id, "merge must keep the original row id"
    assert second.event_count == 2
    assert second.wake_version == first_wake + 1, "wakeable merge bumps wake_version"
    assert second.read_at is None, "merge resets read_at"
    assert second.last_event_at is not None


def test_upsert_digest_merge_does_not_bump_wake_version(db_session):
    project = _project(db_session)
    recipient = _agent(db_session, project, "host")
    target_id = uuid.uuid4()
    common = dict(
        recipient_agent_id=recipient.id,
        project_id=None,
        event="experiment.lock.no_progress",
        summary="locked",
        target_type="experiment",
        target_id=target_id,
        payload={"experiment_id": "e1"},
    )
    first = notification_service._upsert_notification(
        db_session, category=notification_service.NotificationCategory.digest, **common
    )
    db_session.commit()
    first_wake = first.wake_version

    second = notification_service._upsert_notification(
        db_session, category=notification_service.NotificationCategory.digest, **common
    )
    db_session.commit()
    assert second.event_count == 2
    assert second.wake_version == first_wake, "digest merge must not bump wake_version"


def test_upsert_preserves_fingerprint_version_on_merge(db_session):
    project = _project(db_session)
    recipient = _agent(db_session, project, "host")
    target_id = uuid.uuid4()
    common = dict(
        recipient_agent_id=recipient.id,
        project_id=None,
        event="experiment.lock.no_progress",
        summary="locked",
        target_type="experiment",
        target_id=target_id,
        payload={"experiment_id": "e1"},
        category=notification_service.NotificationCategory.wakeable,
    )
    first = notification_service._upsert_notification(db_session, **common)
    db_session.commit()
    # Simulate a legacy v1 row by mutating the persisted column.
    first.fingerprint_version = NotificationFingerprintVersion.v1
    db_session.commit()

    second = notification_service._upsert_notification(db_session, **common)
    db_session.commit()
    assert second.fingerprint_version == NotificationFingerprintVersion.v1


def test_upsert_returns_persisted_row_with_new_count(db_session):
    """The RETURNING clause must surface the post-merge row, not the
    pre-merge snapshot — callers depend on ``wake_version`` / ``event_count``
    after a wake bump."""
    project = _project(db_session)
    recipient = _agent(db_session, project, "host")
    target_id = uuid.uuid4()
    common = dict(
        recipient_agent_id=recipient.id,
        project_id=None,
        event="experiment.lock.no_progress",
        summary="locked",
        target_type="experiment",
        target_id=target_id,
        payload={"experiment_id": "e1"},
        category=notification_service.NotificationCategory.wakeable,
    )
    first = notification_service._upsert_notification(db_session, **common)
    db_session.commit()
    db_session.expire(first)
    second = notification_service._upsert_notification(db_session, **common)
    assert second.event_count == 2
    assert second.wake_version == 2
