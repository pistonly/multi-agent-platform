"""Tests for authz experiment (0e6926fa) PR2 — capability gate on cross_persona_call.

Pins:
1. ``Agent.has_capability`` derives from role + name suffix.
2. ``POST /experiments/{id}/cross-persona-call``:
   * host persona → 201, audit row has no ``rejected`` key
   * admin → 201, audit row has no ``rejected`` key
   * participant → 403, audit row written with ``rejected=True`` + reason
3. ``audit_service.log_cross_persona_call_no_commit(..., rejected=True)``
   serializes ``rejected`` and ``rejection_reason`` into payload_json.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from map_types.enums import AgentRole
from server.domain.models import Agent, AuditLog, Project
from server.services import audit_service
from server.services.audit_service import CROSS_PERSONA_CALL
from tests._frontmatter import make_valid_plan


# ---------------------------------------------------------------------------
# (1) Agent.has_capability
# ---------------------------------------------------------------------------


def _agent(name: str, role: AgentRole = AgentRole.agent, project_id: uuid.UUID | None = None) -> Agent:
    return Agent(
        name=name,
        role=role,
        api_token_hash="x" * 64,
        api_token_prefix=name[:8],
        project_id=project_id,
    )


def test_has_capability_admin_passes_system_namespace(db_session):
    admin = _agent("some-admin", role=AgentRole.admin)
    db_session.add(admin)
    db_session.flush()
    assert admin.has_capability("system:scan_stalled") is True
    assert admin.has_capability("system:audit_export") is True
    assert admin.has_capability("system:cross_persona_call") is True


def test_has_capability_host_passes_persona_scoped_caps(db_session):
    host = _agent("multi-agents-platform-host")
    db_session.add(host)
    db_session.flush()
    assert host.persona == "host"
    assert host.has_capability("system:scan_stalled") is True
    assert host.has_capability("system:audit_export") is True
    assert host.has_capability("system:cross_persona_call") is True
    # host does NOT auto-pass unrelated namespaces
    assert host.has_capability("review:submit") is False


def test_has_capability_participant_only_topic_comment(db_session):
    p = _agent("multi-agents-platform-participant")
    db_session.add(p)
    db_session.flush()
    assert p.persona == "participant"
    assert p.has_capability("topic:comment") is True
    assert p.has_capability("system:scan_stalled") is False
    assert p.has_capability("system:cross_persona_call") is False


def test_has_capability_reviewer_passes_review_caps(db_session):
    r = _agent("multi-agents-platform-reviewer")
    db_session.add(r)
    db_session.flush()
    assert r.persona == "reviewer"
    assert r.has_capability("review:submit") is True
    assert r.has_capability("review:accept_result") is True
    assert r.has_capability("system:cross_persona_call") is False


def test_has_capability_unknown_name_returns_false(db_session):
    a = _agent("random-person")
    db_session.add(a)
    db_session.flush()
    assert a.persona is None
    assert a.has_capability("anything") is False


# ---------------------------------------------------------------------------
# (2) audit_service rejected branch
# ---------------------------------------------------------------------------


def test_log_cross_persona_call_no_commit_rejected_branch(db_session, project):
    """``rejected=True`` writes ``rejected`` + ``rejection_reason`` into
    ``payload_json``. Default branch leaves the payload as before."""
    caller = _agent("multi-agents-platform-participant")
    db_session.add(caller)
    db_session.flush()
    exp_id = uuid.uuid4()
    entry = audit_service.log_cross_persona_call_no_commit(
        db_session,
        caller_agent_id=caller.id,
        target_experiment_id=exp_id,
        project_id=uuid.UUID(project["id"]),
        visibility_diff={"a": 1},
        result_partition_count=1,
        diff_size=1,
        rejected=True,
        rejection_reason="missing capability system:cross_persona_call",
    )
    db_session.commit()
    db_session.refresh(entry)
    assert entry.payload_json["rejected"] is True
    assert "missing capability" in entry.payload_json["rejection_reason"]

    # Default branch — no rejected key in payload.
    entry2 = audit_service.log_cross_persona_call_no_commit(
        db_session,
        caller_agent_id=caller.id,
        target_experiment_id=exp_id,
        project_id=uuid.UUID(project["id"]),
        visibility_diff={},
        result_partition_count=0,
        diff_size=0,
    )
    db_session.commit()
    db_session.refresh(entry2)
    assert "rejected" not in entry2.payload_json


# ---------------------------------------------------------------------------
# (3) Endpoint capability gate
# ---------------------------------------------------------------------------


def _create_persona_agent(
    client: TestClient, admin_headers: dict, project_id: str, project_key: str, name: str
) -> dict:
    response = client.post(
        "/api/v1/agents",
        headers=admin_headers,
        params={"name": name, "role": "agent", "project_key": project_key},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _create_experiment(client: TestClient, headers: dict, project_id: str, title: str) -> str:
    response = client.post(
        f"/api/v1/projects/{project_id}/experiments",
        headers=headers,
        json={"title": title, "plan": {"content_md": make_valid_plan(body="## 计划")}},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_cross_persona_call_rejects_participant_with_audit(
    client: TestClient,
    admin_headers: dict,
    db_session,
    project,
):
    """participant attempts to record a cross_persona_call → 403 + audit
    row with rejected=True.

    We create the host + experiment via admin, then create a separate
    participant agent and try the cross-persona-call as them.
    """
    # Create experiment as host.
    host = _create_persona_agent(
        client, admin_headers, project["id"], project["project_key"],
        "multi-agents-platform-host",
    )
    host_headers = {"Authorization": f"Bearer {host['api_token']}"}
    exp_id = _create_experiment(
        client, host_headers, project["id"], "capability gate participant reject"
    )
    # Create a participant agent and try the audit endpoint.
    participant = _create_persona_agent(
        client, admin_headers, project["id"], project["project_key"],
        "multi-agents-platform-participant",
    )
    participant_headers = {"Authorization": f"Bearer {participant['api_token']}"}

    payload = {
        "visibility_diff": {"actions": {"host": ["complete"], "participant": []}},
        "result_partition_count": 2,
        "diff_size": 1,
    }
    response = client.post(
        f"/api/v1/experiments/{exp_id}/cross-persona-call",
        headers=participant_headers,
        json=payload,
    )
    assert response.status_code == 403, response.text
    # Audit row was written with rejected=True
    rows = db_session.scalars(
        select(AuditLog).where(AuditLog.action == CROSS_PERSONA_CALL)
    ).all()
    assert len(rows) == 1
    assert rows[0].payload_json["rejected"] is True
    assert "missing capability" in rows[0].payload_json["rejection_reason"]


def test_cross_persona_call_passes_for_host(
    client: TestClient,
    admin_headers: dict,
    db_session,
    project,
):
    """host persona → 201 + clean audit row (no rejected key)."""
    host = _create_persona_agent(
        client, admin_headers, project["id"], project["project_key"],
        "multi-agents-platform-host",
    )
    host_headers = {"Authorization": f"Bearer {host['api_token']}"}
    exp_id = _create_experiment(
        client, host_headers, project["id"], "capability gate host pass"
    )
    payload = {
        "visibility_diff": {"actions": {"host": ["complete"], "reviewer": []}},
        "result_partition_count": 2,
        "diff_size": 1,
    }
    response = client.post(
        f"/api/v1/experiments/{exp_id}/cross-persona-call",
        headers=host_headers,
        json=payload,
    )
    assert response.status_code == 201, response.text
    rows = db_session.scalars(
        select(AuditLog).where(AuditLog.action == CROSS_PERSONA_CALL)
    ).all()
    assert len(rows) == 1
    assert "rejected" not in rows[0].payload_json
