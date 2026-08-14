"""Experiment executor delegation (migration 042).

Pins the host/executor permission split:

1. ``start_experiment`` without ``--executor`` defaults to host
   self-executes (``executor_agent_id == actor.id``).
2. ``start_experiment --executor <agent>`` delegates execution; the
   designated agent becomes the sole non-admin caller allowed to
   ``complete``.
3. After delegation, the host (creator) loses ``complete`` permission
   but keeps every other lifecycle gate (cancel / withdraw / etc).
4. The executor is structurally forbidden from accepting their own
   result — the reviewer-isolation invariant extends to the executor.
5. The executor is also forbidden from calling ``reject-result`` on
   their own result (gets ``REVIEW_REJECT_RESULT_MISUSE``).
6. Cross-project executor delegation is rejected with 403.
7. Legacy experiments (``executor_agent_id IS NULL``) keep the
   pre-042 host-self-executes behavior — verified at the service layer
   because new API experiments always populate the column at start time.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from server.domain.models import Agent, AgentRole, Experiment, ExperimentPhase
from server.services import phase_service
from server.services.errors import ForbiddenError
from tests._frontmatter import make_valid_plan

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def participant(
    client: TestClient, project: dict, admin_headers: dict[str, str]
) -> dict[str, str]:
    """A second project-bound agent (the participant persona) for executor delegation tests."""
    response = client.post(
        "/api/v1/agents",
        headers=admin_headers,
        json={
            "name": "participant-executor",
            "role": "agent",
            "project_key": project["project_key"],
        },
    )
    assert response.status_code == 201
    data = response.json()
    return {
        "id": data["id"],
        "headers": {"Authorization": f"Bearer {data['api_token']}"},
    }


@pytest.fixture
def approved_experiment(
    client: TestClient, auth_headers: dict[str, str], reviewer: dict, project: dict
) -> dict:
    """An experiment in the ``approved`` phase, ready for ``start``."""
    experiment = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={
            "title": "执行实验",
            "plan": {"content_md": make_valid_plan(body="## 计划")},
            "submit_for_review": True,
        },
    ).json()
    exp_id = experiment["id"]

    review = client.post(
        f"/api/v1/experiments/{exp_id}/reviews",
        headers=reviewer["headers"],
        json={"reasonable_items": ["OK"]},
    ).json()
    for item in review["items"]:
        if item["kind"] == "unreasonable":
            client.patch(
                f"/api/v1/review-items/{item['id']}",
                headers=reviewer["headers"],
                json={"status": "resolved"},
            )

    client.post(f"/api/v1/experiments/{exp_id}/approve", headers=auth_headers)
    return {"project_id": project["id"], "experiment_id": exp_id}


def _complete_payload() -> dict:
    return {
        "summary": "实验完成",
        "content_md": "## 结果\n基线噪声 0.02",
        "metadata": {"metric": 0.02, "pytest_summary": "unit passed"},
    }


# ---------------------------------------------------------------------------
# (1) start without --executor: host self-executes
# ---------------------------------------------------------------------------


def test_start_without_executor_defaults_to_host(
    client: TestClient, auth_headers: dict, approved_experiment: dict
):
    """Omitting ``--executor`` populates ``executor_agent_id`` with the host's id."""
    exp_id = approved_experiment["experiment_id"]
    started = client.post(f"/api/v1/experiments/{exp_id}/start", headers=auth_headers)
    assert started.status_code == 200
    body = started.json()
    assert body["phase"] == "running"
    # executor_agent_id must be populated and equal to the host (creator).
    assert body["executor_agent_id"] is not None
    assert body["executor_agent_id"] == body["creator_agent_id"]


def test_start_with_empty_body_still_self_executes(
    client: TestClient, auth_headers: dict, approved_experiment: dict
):
    """Posting an empty body must behave identically to no body (back-compat)."""
    exp_id = approved_experiment["experiment_id"]
    started = client.post(
        f"/api/v1/experiments/{exp_id}/start",
        headers=auth_headers,
        json={},
    )
    assert started.status_code == 200
    body = started.json()
    assert body["executor_agent_id"] == body["creator_agent_id"]


# ---------------------------------------------------------------------------
# (2) start with --executor: delegation populates executor_agent_id
# ---------------------------------------------------------------------------


def test_start_with_executor_delegates_to_participant(
    client: TestClient, auth_headers: dict, participant: dict, approved_experiment: dict
):
    """Passing ``executor_agent_id`` designates that agent as the executor."""
    exp_id = approved_experiment["experiment_id"]
    started = client.post(
        f"/api/v1/experiments/{exp_id}/start",
        headers=auth_headers,
        json={"executor_agent_id": participant["id"]},
    )
    assert started.status_code == 200
    body = started.json()
    assert body["phase"] == "running"
    assert body["executor_agent_id"] == participant["id"]
    assert body["executor_agent_id"] != body["creator_agent_id"]


def test_start_with_executor_uuid_string_accepted(
    client: TestClient, auth_headers: dict, participant: dict, approved_experiment: dict
):
    """The API accepts executor_agent_id as a UUID string in the JSON body."""
    exp_id = approved_experiment["experiment_id"]
    started = client.post(
        f"/api/v1/experiments/{exp_id}/start",
        headers=auth_headers,
        json={"executor_agent_id": str(participant["id"])},
    )
    assert started.status_code == 200
    assert started.json()["executor_agent_id"] == participant["id"]


# ---------------------------------------------------------------------------
# (3) After delegation: host loses complete, executor gains it
# ---------------------------------------------------------------------------


def test_host_cannot_complete_after_delegation(
    client: TestClient, auth_headers: dict, participant: dict, approved_experiment: dict
):
    """Once execution is delegated, the host (creator) is forbidden from complete."""
    exp_id = approved_experiment["experiment_id"]
    client.post(
        f"/api/v1/experiments/{exp_id}/start",
        headers=auth_headers,
        json={"executor_agent_id": participant["id"]},
    )

    response = client.post(
        f"/api/v1/experiments/{exp_id}/complete",
        headers=auth_headers,
        json=_complete_payload(),
    )
    assert response.status_code == 403
    assert "executor" in response.json()["detail"].lower()


def test_executor_can_complete_after_delegation(
    client: TestClient, auth_headers: dict, participant: dict, approved_experiment: dict
):
    """The designated executor can call complete; host cannot."""
    exp_id = approved_experiment["experiment_id"]
    client.post(
        f"/api/v1/experiments/{exp_id}/start",
        headers=auth_headers,
        json={"executor_agent_id": participant["id"]},
    )

    completed = client.post(
        f"/api/v1/experiments/{exp_id}/complete",
        headers=participant["headers"],
        json=_complete_payload(),
    )
    assert completed.status_code == 200
    assert completed.json()["phase"] == "result_review"


def test_third_party_cannot_complete_after_delegation(
    client: TestClient,
    auth_headers: dict,
    participant: dict,
    reviewer: dict,
    approved_experiment: dict,
):
    """Neither the host nor an unrelated project member can complete once delegated."""
    exp_id = approved_experiment["experiment_id"]
    client.post(
        f"/api/v1/experiments/{exp_id}/start",
        headers=auth_headers,
        json={"executor_agent_id": participant["id"]},
    )

    # Reviewer (project member, not executor, not admin) → 403
    reviewer_attempt = client.post(
        f"/api/v1/experiments/{exp_id}/complete",
        headers=reviewer["headers"],
        json=_complete_payload(),
    )
    assert reviewer_attempt.status_code == 403


# ---------------------------------------------------------------------------
# (4) Executor cannot self-review (extended _ensure_result_reviewer)
# ---------------------------------------------------------------------------


def test_executor_cannot_accept_own_result(
    client: TestClient, auth_headers: dict, participant: dict, approved_experiment: dict
):
    """The executor is blocked from accepting their own result."""
    exp_id = approved_experiment["experiment_id"]
    client.post(
        f"/api/v1/experiments/{exp_id}/start",
        headers=auth_headers,
        json={"executor_agent_id": participant["id"]},
    )
    client.post(
        f"/api/v1/experiments/{exp_id}/complete",
        headers=participant["headers"],
        json=_complete_payload(),
    )

    self_accept = client.post(
        f"/api/v1/experiments/{exp_id}/accept-result",
        headers=participant["headers"],
        json={"summary": "自审通过", "content_md": "不允许"},
    )
    assert self_accept.status_code == 403


# ---------------------------------------------------------------------------
# (5) Executor cannot reject own result (REVIEW_REJECT_RESULT_MISUSE)
# ---------------------------------------------------------------------------


def test_executor_cannot_reject_own_result(
    client: TestClient, auth_headers: dict, participant: dict, approved_experiment: dict
):
    """The executor gets REVIEW_REJECT_RESULT_MISUSE when calling reject-result."""
    exp_id = approved_experiment["experiment_id"]
    client.post(
        f"/api/v1/experiments/{exp_id}/start",
        headers=auth_headers,
        json={"executor_agent_id": participant["id"]},
    )
    client.post(
        f"/api/v1/experiments/{exp_id}/complete",
        headers=participant["headers"],
        json=_complete_payload(),
    )

    rejected = client.post(
        f"/api/v1/experiments/{exp_id}/reject-result",
        headers=participant["headers"],
        json={"summary": "executor 自驳", "content_md": "误用 reject-result"},
    )
    assert rejected.status_code == 422
    body = rejected.json()
    assert body["error_code"] == "REVIEW_REJECT_RESULT_MISUSE"


# ---------------------------------------------------------------------------
# (6) Cross-project executor delegation is rejected
# ---------------------------------------------------------------------------


def test_cross_project_executor_rejected(
    client: TestClient,
    admin_headers: dict,
    auth_headers: dict,
    approved_experiment: dict,
):
    """Delegating to an agent in another project is rejected with 403."""
    exp_id = approved_experiment["experiment_id"]

    # Create a second project + agent in it.
    other_project_resp = client.post(
        "/api/v1/projects",
        headers=admin_headers,
        json={
            "project_key": "other-project-exec",
            "name": "Other Project",
            "workspace_path": "/tmp/other-project-exec",
        },
    )
    assert other_project_resp.status_code == 201, other_project_resp.text
    other_agent_resp = client.post(
        "/api/v1/agents",
        headers=admin_headers,
        json={
            "name": "other-project-agent",
            "role": "agent",
            "project_key": "other-project-exec",
        },
    )
    assert other_agent_resp.status_code == 201, other_agent_resp.text
    other_agent_id = other_agent_resp.json()["id"]

    response = client.post(
        f"/api/v1/experiments/{exp_id}/start",
        headers=auth_headers,
        json={"executor_agent_id": other_agent_id},
    )
    assert response.status_code == 403
    assert "same project" in response.json()["detail"]


# ---------------------------------------------------------------------------
# (7) Host retains other lifecycle gates after delegation
# ---------------------------------------------------------------------------


def test_host_can_cancel_after_delegation(
    client: TestClient, auth_headers: dict, participant: dict, approved_experiment: dict
):
    """The host retains cancel authority even after delegating execution."""
    exp_id = approved_experiment["experiment_id"]
    client.post(
        f"/api/v1/experiments/{exp_id}/start",
        headers=auth_headers,
        json={"executor_agent_id": participant["id"]},
    )

    cancelled = client.post(
        f"/api/v1/experiments/{exp_id}/cancel",
        headers=auth_headers,
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["phase"] == "cancelled"


def test_executor_cannot_cancel(
    client: TestClient, auth_headers: dict, participant: dict, approved_experiment: dict
):
    """The executor cannot cancel — that gate stays on the host."""
    exp_id = approved_experiment["experiment_id"]
    client.post(
        f"/api/v1/experiments/{exp_id}/start",
        headers=auth_headers,
        json={"executor_agent_id": participant["id"]},
    )

    response = client.post(
        f"/api/v1/experiments/{exp_id}/cancel",
        headers=participant["headers"],
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# (8) Legacy backward-compat: executor_agent_id=NULL falls back to creator
# ---------------------------------------------------------------------------


def _make_legacy_agent(db: Session, *, project_id: uuid.UUID, name: str) -> Agent:
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


def test_legacy_experiment_executor_null_falls_back_to_creator(
    db_session: Session, project: dict
):
    """Service-layer test: experiments with executor_agent_id=NULL still
    allow the creator (host) to call complete.

    New API experiments always populate executor_agent_id at start time,
    so the NULL fallback only matters for experiments created before
    migration 042. We verify the fallback directly at the service layer.
    """
    project_id = uuid.UUID(project["id"])
    host_agent = _make_legacy_agent(db_session, project_id=project_id, name="legacy-host")

    # Build a legacy experiment directly via ORM (skip start_experiment
    # so executor_agent_id stays NULL).
    experiment = Experiment(
        id=uuid.uuid4(),
        project_id=project_id,
        creator_agent_id=host_agent.id,
        title="legacy",
        phase=ExperimentPhase.running,
        phase_owner="host",
        # executor_agent_id intentionally NULL — simulates pre-042 data.
    )
    db_session.add(experiment)
    db_session.commit()

    # The host (creator) must still be allowed to complete —
    # _ensure_can_complete falls back to creator_agent_id.
    phase_service._ensure_can_complete(experiment, host_agent)

    # A different agent (non-admin) must still be forbidden.
    other_agent = _make_legacy_agent(
        db_session, project_id=project_id, name="legacy-other"
    )
    with pytest.raises(ForbiddenError):
        phase_service._ensure_can_complete(experiment, other_agent)


# ---------------------------------------------------------------------------
# (9) Reviewer still works after delegation (full happy-path)
# ---------------------------------------------------------------------------


def test_full_delegated_flow_reviewer_accepts_result(
    client: TestClient,
    auth_headers: dict,
    participant: dict,
    reviewer: dict,
    approved_experiment: dict,
):
    """End-to-end: host delegates → executor completes → reviewer accepts."""
    exp_id = approved_experiment["experiment_id"]
    client.post(
        f"/api/v1/experiments/{exp_id}/start",
        headers=auth_headers,
        json={"executor_agent_id": participant["id"]},
    )
    client.post(
        f"/api/v1/experiments/{exp_id}/complete",
        headers=participant["headers"],
        json=_complete_payload(),
    )

    accepted = client.post(
        f"/api/v1/experiments/{exp_id}/accept-result",
        headers=reviewer["headers"],
        json={
            "summary": "结果审批通过",
            "content_md": "结果满足计划验收标准",
            "metadata": {"approved": True},
        },
    )
    assert accepted.status_code == 200
    assert accepted.json()["phase"] == "done"
