from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from map_types.enums import ExperimentPhase, NotificationCategory
from sqlalchemy import select

from server.domain.models import Agent, AgentRole, Experiment, ExperimentLog, Notification, Project
from server.services.auth import create_agent
from server.services.notification_service import notify_stalled_experiment_locks


def _agent(db_session, project, name: str) -> Agent:
    agent, _ = create_agent(db_session, name, AgentRole.agent, project_id=project.id)
    return agent


def _project(db_session) -> Project:
    project = Project(project_key="lock-alert", name="Lock Alert", workspace_path="/tmp/lock-alert")
    db_session.add(project)
    db_session.flush()
    return project


def _running_locked_experiment(db_session, project, host, *, acquired_at, ttl_seconds=1000) -> Experiment:
    exp = Experiment(
        project_id=project.id,
        creator_agent_id=host.id,
        title="stalled lock",
        phase=ExperimentPhase.running,
        current_plan_version=1,
        lock_holder_experiment_id=None,
        lock_acquired_at=acquired_at,
        lock_ttl_seconds=ttl_seconds,
    )
    db_session.add(exp)
    db_session.flush()
    exp.lock_holder_experiment_id = exp.id
    db_session.flush()
    return exp


def test_stalled_lock_sends_wakeable_to_holder_and_digest_to_project_members(db_session):
    project = _project(db_session)
    host = _agent(db_session, project, "host")
    reviewer = _agent(db_session, project, "reviewer")
    now = datetime(2026, 7, 7, 8, 0, tzinfo=UTC)
    exp = _running_locked_experiment(
        db_session,
        project,
        host,
        acquired_at=now - timedelta(seconds=900),
        ttl_seconds=1000,
    )

    notify_stalled_experiment_locks(db_session, now=now)

    host_notifs = db_session.scalars(
        select(Notification).where(Notification.recipient_agent_id == host.id)
    ).all()
    reviewer_notifs = db_session.scalars(
        select(Notification).where(Notification.recipient_agent_id == reviewer.id)
    ).all()
    assert len(host_notifs) == 1
    assert host_notifs[0].event == "experiment.lock.no_progress"
    assert host_notifs[0].target_id == exp.id
    assert host_notifs[0].category == NotificationCategory.wakeable
    assert host_notifs[0].payload_json["elapsed_seconds"] == 900
    assert len(reviewer_notifs) == 1
    assert reviewer_notifs[0].event == "experiment.lock.no_progress"
    assert reviewer_notifs[0].target_id == exp.id
    assert reviewer_notifs[0].category == NotificationCategory.digest


def test_stalled_lock_digest_before_wake_threshold(db_session):
    project = _project(db_session)
    host = _agent(db_session, project, "host")
    reviewer = _agent(db_session, project, "reviewer")
    now = datetime(2026, 7, 7, 8, 0, tzinfo=UTC)
    _running_locked_experiment(
        db_session,
        project,
        host,
        acquired_at=now - timedelta(seconds=600),
        ttl_seconds=1000,
    )

    notify_stalled_experiment_locks(db_session, now=now)

    host_notifs = db_session.scalars(
        select(Notification).where(Notification.recipient_agent_id == host.id)
    ).all()
    reviewer_notifs = db_session.scalars(
        select(Notification).where(Notification.recipient_agent_id == reviewer.id)
    ).all()
    assert host_notifs == []
    assert len(reviewer_notifs) == 1
    assert reviewer_notifs[0].category == NotificationCategory.digest


def test_stalled_lock_skips_when_progress_log_exists_after_lock(db_session):
    project = _project(db_session)
    host = _agent(db_session, project, "host")
    reviewer = _agent(db_session, project, "reviewer")
    now = datetime(2026, 7, 7, 8, 0, tzinfo=UTC)
    exp = _running_locked_experiment(
        db_session,
        project,
        host,
        acquired_at=now - timedelta(seconds=900),
        ttl_seconds=1000,
    )
    db_session.add(
        ExperimentLog(
            experiment_id=exp.id,
            author_agent_id=host.id,
            summary="progress",
            content_md="made progress",
            log_index=1,
            created_at=now - timedelta(seconds=100),
        )
    )
    db_session.flush()

    emitted = notify_stalled_experiment_locks(db_session, now=now)

    assert emitted == []
    assert db_session.scalars(select(Notification)).all() == []
    assert host.id != reviewer.id


def test_stalled_lock_skips_released_and_non_running_experiments(db_session):
    project = _project(db_session)
    host = _agent(db_session, project, "host")
    now = datetime(2026, 7, 7, 8, 0, tzinfo=UTC)
    released = _running_locked_experiment(
        db_session,
        project,
        host,
        acquired_at=now - timedelta(seconds=900),
        ttl_seconds=1000,
    )
    released.lock_holder_experiment_id = None
    released.lock_acquired_at = None
    released.lock_ttl_seconds = None
    approved = _running_locked_experiment(
        db_session,
        project,
        host,
        acquired_at=now - timedelta(seconds=900),
        ttl_seconds=1000,
    )
    approved.phase = ExperimentPhase.approved
    db_session.flush()

    emitted = notify_stalled_experiment_locks(db_session, now=now)

    assert emitted == []
    assert db_session.scalars(select(Notification)).all() == []


def test_stalled_lock_scan_can_be_scoped_to_project(db_session):
    project = _project(db_session)
    other_project = Project(project_key="other-lock-alert", name="Other Lock Alert", workspace_path="/tmp/other")
    db_session.add(other_project)
    db_session.flush()
    host = _agent(db_session, project, "host")
    reviewer = _agent(db_session, project, "reviewer")
    other_host = _agent(db_session, other_project, "other-host")
    now = datetime(2026, 7, 7, 8, 0, tzinfo=UTC)
    exp = _running_locked_experiment(
        db_session,
        project,
        host,
        acquired_at=now - timedelta(seconds=900),
        ttl_seconds=1000,
    )
    other_exp = _running_locked_experiment(
        db_session,
        other_project,
        other_host,
        acquired_at=now - timedelta(seconds=900),
        ttl_seconds=1000,
    )

    notify_stalled_experiment_locks(db_session, project_id=project.id, now=now)

    notifications = db_session.scalars(select(Notification)).all()
    assert {row.target_id for row in notifications} == {exp.id}
    assert other_exp.id not in {row.target_id for row in notifications}
    assert {row.recipient_agent_id for row in notifications} == {host.id, reviewer.id}


def test_stalled_lock_scan_endpoint_host_scopes_to_own_project(
    client, admin_headers, db_session
):
    project_a = client.post(
        "/api/v1/projects",
        headers=admin_headers,
        json={"project_key": "scan-a", "name": "Scan A", "workspace_path": "/tmp/scan-a"},
    ).json()
    project_b = client.post(
        "/api/v1/projects",
        headers=admin_headers,
        json={"project_key": "scan-b", "name": "Scan B", "workspace_path": "/tmp/scan-b"},
    ).json()
    host_a_resp = client.post(
        "/api/v1/agents",
        headers=admin_headers,
        params={
            "name": "multi-agent-platform-host",
            "role": "agent",
            "project_key": project_a["project_key"],
        },
    )
    assert host_a_resp.status_code == 201
    host_a = db_session.get(Agent, uuid.UUID(host_a_resp.json()["id"]))
    host_b = _agent(db_session, db_session.get(Project, uuid.UUID(project_b["id"])), "host-b")
    now = datetime.now(UTC)
    exp_a = _running_locked_experiment(
        db_session,
        db_session.get(Project, uuid.UUID(project_a["id"])),
        host_a,
        acquired_at=now - timedelta(seconds=900),
        ttl_seconds=1000,
    )
    exp_b = _running_locked_experiment(
        db_session,
        db_session.get(Project, uuid.UUID(project_b["id"])),
        host_b,
        acquired_at=now - timedelta(seconds=900),
        ttl_seconds=1000,
    )

    response = client.post(
        "/api/v1/experiments/lock/scan-stalled",
        headers={"Authorization": f"Bearer {host_a_resp.json()['api_token']}"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["emitted_count"] == 1
    notifications = db_session.scalars(select(Notification)).all()
    assert {row.target_id for row in notifications} == {exp_a.id}
    assert exp_b.id not in {row.target_id for row in notifications}
