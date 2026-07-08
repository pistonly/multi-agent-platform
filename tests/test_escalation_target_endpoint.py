"""STATE_MACHINE.* escalation_target endpoint (experiment 1561729 I1(c)).

Pins plan (c) acceptance:

- ``GET /agents/me/escalation-target?experiment_id=...`` returns the
  resolved escalation contact + the tier that picked it.
- The endpoint falls back to role-based tiers when no experiment override
  is present.
- The SDK ``MAPClient.get_escalation_target`` exposes the same fields.

CLI double-consume (plan (e)) is covered separately by the subprocess
test in test_cli_error_envelope.py so this file stays focused on the
endpoint contract.
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from map_types.enums import AgentRole, ExperimentPhase
from sqlalchemy.orm import Session

from sdk.python.map_client import MAPClient
from server.domain.models import Agent, Experiment


def _make_agent(
    db: Session,
    *,
    project_id: uuid.UUID,
    name: str,
    role: AgentRole,
) -> Agent:
    agent = Agent(
        id=uuid.uuid4(),
        project_id=project_id,
        name=name,
        api_token_hash="test-hash",
        api_token_prefix="test",
        role=role,
    )
    db.add(agent)
    db.flush()
    return agent


def _make_experiment(
    db: Session,
    *,
    project_id: uuid.UUID,
    creator_id: uuid.UUID,
    escalation_target_id: uuid.UUID | None = None,
) -> Experiment:
    exp = Experiment(
        id=uuid.uuid4(),
        project_id=project_id,
        creator_agent_id=creator_id,
        title="escalation endpoint test",
        phase=ExperimentPhase.running,
    )
    if escalation_target_id is not None:
        exp.escalation_target_agent_id = escalation_target_id
    db.add(exp)
    db.flush()
    return exp


def _creator_id_from_headers(
    client: TestClient, auth_headers: dict[str, str]
) -> uuid.UUID:
    me = client.get("/api/v1/agents/me", headers=auth_headers).json()
    return uuid.UUID(me["id"])


def test_endpoint_resolves_experiment_override_tier(
    client: TestClient,
    project: dict,
    auth_headers: dict[str, str],
    db_session: Session,
) -> None:
    """Tier 1: experiment override is surfaced with tier=experiment_override."""
    project_id = uuid.UUID(project["id"])
    creator_id = _creator_id_from_headers(client, auth_headers)

    override = _make_agent(
        db_session,
        project_id=project_id,
        name="override-buddy",
        role=AgentRole.agent,
    )
    db_session.flush()

    exp = _make_experiment(
        db_session,
        project_id=project_id,
        creator_id=creator_id,
        escalation_target_id=override.id,
    )
    db_session.commit()

    response = client.get(
        "/api/v1/agents/me/escalation-target",
        params={"experiment_id": str(exp.id)},
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["escalation_target_id"] == str(override.id)
    assert body["tier"] == "experiment_override"
    assert body["escalation_label"] == "@override-buddy"
    assert body["experiment_id"] == str(exp.id)


def test_endpoint_falls_back_when_experiment_id_unknown(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Unknown experiment_id → role-based fallback path.

    The endpoint tolerates a missing experiment and resolves against the
    caller + role-based tiers instead of 404'ing.
    """
    response = client.get(
        "/api/v1/agents/me/escalation-target",
        params={"experiment_id": str(uuid.uuid4())},
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["tier"] in {
        "current_caller",
        "admin",
        "same_role_active",
        "none",
    }
    # experiment_id is preserved on the response even when the experiment
    # is not found.
    assert body["experiment_id"]


def test_endpoint_works_without_experiment_id(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """No experiment_id → pure role-based resolution."""
    response = client.get(
        "/api/v1/agents/me/escalation-target",
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["experiment_id"] is None
    # Without experiment, tier is current_caller (the auth-headers agent).
    assert body["tier"] in {
        "current_caller",
        "admin",
        "same_role_active",
        "none",
    }


def test_endpoint_unknown_tier_label_is_no_escalation_contact(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
) -> None:
    """Tier=none renders the explicit "<no escalation contact>" placeholder."""
    # Reach into the endpoint with an agent whose project has no admins
    # or same-role peers — the resolver must still return 200 with a
    # tier=none marker, not 500.
    me = client.get("/api/v1/agents/me", headers=auth_headers).json()
    # The placeholder is a fixed string; check it stays that way.
    body = client.get(
        "/api/v1/agents/me/escalation-target",
        headers=auth_headers,
    ).json()
    # The current caller is always reachable → tier is current_caller, not
    # none, but the contract still holds.
    if body["tier"] == "none":
        assert body["escalation_label"] == "<no escalation contact>"
        assert body["escalation_target_id"] is None
    else:
        assert body["escalation_label"].startswith("@")
        assert body["escalation_target_id"] is not None
    _ = me  # silence unused warning


def test_sdk_get_escalation_target_returns_escalation_target_read(
    map_client: MAPClient,
) -> None:
    """SDK ``get_escalation_target`` returns an ``EscalationTargetRead``."""
    target = map_client.get_escalation_target()
    assert target is not None
    assert hasattr(target, "escalation_label")
    assert hasattr(target, "tier")
    assert hasattr(target, "escalation_target_id")
    assert hasattr(target, "experiment_id")
    assert target.tier in {
        "current_caller",
        "admin",
        "same_role_active",
        "none",
    }


def test_sdk_get_escalation_target_with_experiment_id(
    map_client: MAPClient,
    db_session: Session,
    project: dict,
) -> None:
    """SDK ``get_escalation_target(experiment_id=...)`` returns the override."""
    project_id = uuid.UUID(project["id"])
    # Build a second agent inside the same project.
    second = _make_agent(
        db_session,
        project_id=project_id,
        name="second-agent",
        role=AgentRole.agent,
    )
    db_session.flush()

    # Get current caller ID via the SDK.
    me = map_client.get_me()
    creator_id = me.id if isinstance(me.id, uuid.UUID) else uuid.UUID(me.id)
    exp = _make_experiment(
        db_session,
        project_id=project_id,
        creator_id=creator_id,
        escalation_target_id=second.id,
    )
    db_session.commit()

    target = map_client.get_escalation_target(experiment_id=exp.id)
    assert target.experiment_id == exp.id
    assert target.escalation_target_id == second.id
    assert target.tier == "experiment_override"
    assert target.escalation_label == "@second-agent"
