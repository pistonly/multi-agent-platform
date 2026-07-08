"""0db51e10 I3(5d): phase visibility whitelist + hidden_for_current_persona.

Unit tests for the per-actor fold semantics introduced by plan v2 (5d):

> phase visibility whitelist 配置化 + hidden_for_current_persona partition field

We pin three behaviors:

1. ``phase_whitelist=None`` (legacy default) keeps every phase visible —
   the previous capability logic continues to apply.
2. A non-empty whitelist folds excluded phases as
   ``([], "hidden_for_current_persona")`` regardless of the actor's role.
3. ``experiment_summary_for_actor`` propagates the fold into the new
   ``hidden_for_current_persona`` partition field, and keeps every other
   per-actor field (actions / blocked_on / phase_owner /
   informational_only / log_count / latest_log_summary / legacy_self_review)
   populated from the underlying capability decision.

These are pure-function unit tests — they call the helpers directly with
ORM fixtures, no FastAPI client / HTTP layer.
"""

from __future__ import annotations

import uuid

import pytest
from map_types.enums import AgentRole, ExperimentPhase
from sqlalchemy.orm import Session

from server.domain.models import Agent, Experiment
from server.services.experiment_capabilities_service import (
    HIDDEN_FOR_CURRENT_PERSONA,
    compute_experiment_capabilities,
    experiment_summary_for_actor,
)

pytestmark = pytest.mark.slow


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_agent(
    db: Session,
    *,
    project_id: uuid.UUID,
    name: str,
    role: AgentRole = AgentRole.agent,
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
    phase: ExperimentPhase,
) -> Experiment:
    exp = Experiment(
        id=uuid.uuid4(),
        project_id=project_id,
        creator_agent_id=creator_id,
        title="whitelist fixture",
        phase=phase,
    )
    db.add(exp)
    db.flush()
    return exp


# ---------------------------------------------------------------------------
# (5d) Whitelist None / empty == legacy behavior
# ---------------------------------------------------------------------------


def test_no_whitelist_keeps_legacy_capabilities(
    db_session: Session, project: dict
) -> None:
    """``phase_whitelist=None`` → every phase visible to every role.

    Locks the backward-compatible default: a creator in ``draft`` still
    sees ``["submit_for_review"]`` regardless of any future whitelist
    toggle.
    """
    project_id = uuid.UUID(project["id"])
    creator = _make_agent(db_session, project_id=project_id, name="creator")
    exp = _make_experiment(
        db_session,
        project_id=project_id,
        creator_id=creator.id,
        phase=ExperimentPhase.draft,
    )

    actions, blocked_on = compute_experiment_capabilities(
        db_session, exp, creator, phase_whitelist=None
    )
    assert actions == ["submit_for_review"]
    assert blocked_on == "none"


def test_empty_whitelist_keeps_legacy_capabilities(
    db_session: Session, project: dict
) -> None:
    """Empty list == None: every phase visible (treated as "no filter")."""
    project_id = uuid.UUID(project["id"])
    creator = _make_agent(db_session, project_id=project_id, name="creator")
    exp = _make_experiment(
        db_session,
        project_id=project_id,
        creator_id=creator.id,
        phase=ExperimentPhase.draft,
    )

    actions, blocked_on = compute_experiment_capabilities(
        db_session, exp, creator, phase_whitelist=[]
    )
    assert actions == ["submit_for_review"]
    assert blocked_on == "none"


# ---------------------------------------------------------------------------
# (5d) Whitelist excludes current phase → fold
# ---------------------------------------------------------------------------


def test_whitelist_excludes_draft_folds_to_hidden(
    db_session: Session, project: dict
) -> None:
    """``phase_whitelist=[ExperimentPhase.result_review]`` and the
    experiment is in ``draft`` → folded as hidden."""
    project_id = uuid.UUID(project["id"])
    creator = _make_agent(db_session, project_id=project_id, name="creator")
    exp = _make_experiment(
        db_session,
        project_id=project_id,
        creator_id=creator.id,
        phase=ExperimentPhase.draft,
    )

    actions, blocked_on = compute_experiment_capabilities(
        db_session,
        exp,
        creator,
        phase_whitelist=[ExperimentPhase.result_review],
    )
    assert actions == []
    assert blocked_on == HIDDEN_FOR_CURRENT_PERSONA


def test_whitelist_excludes_draft_folds_for_non_creator(
    db_session: Session, project: dict
) -> None:
    """Fold is per-actor: a non-creator (reviewer) in ``draft`` is also
    folded when ``draft`` is excluded by the whitelist."""
    project_id = uuid.UUID(project["id"])
    creator = _make_agent(db_session, project_id=project_id, name="creator")
    reviewer = _make_agent(db_session, project_id=project_id, name="reviewer")
    exp = _make_experiment(
        db_session,
        project_id=project_id,
        creator_id=creator.id,
        phase=ExperimentPhase.draft,
    )

    actions, blocked_on = compute_experiment_capabilities(
        db_session,
        exp,
        reviewer,
        phase_whitelist=[ExperimentPhase.result_review],
    )
    assert actions == []
    assert blocked_on == HIDDEN_FOR_CURRENT_PERSONA


def test_whitelist_includes_current_phase_keeps_capabilities(
    db_session: Session, project: dict
) -> None:
    """When the current phase IS in the whitelist, capabilities are
    computed normally (no fold)."""
    project_id = uuid.UUID(project["id"])
    creator = _make_agent(db_session, project_id=project_id, name="creator")
    exp = _make_experiment(
        db_session,
        project_id=project_id,
        creator_id=creator.id,
        phase=ExperimentPhase.draft,
    )

    actions, blocked_on = compute_experiment_capabilities(
        db_session,
        exp,
        creator,
        phase_whitelist=[ExperimentPhase.draft],
    )
    assert actions == ["submit_for_review"]
    assert blocked_on == "none"


def test_whitelist_accepts_string_phase_names(
    db_session: Session, project: dict
) -> None:
    """Whitelist may contain string phase names (``"result_review"``) —
    the predicate is exact-match against ``phase.value``."""
    project_id = uuid.UUID(project["id"])
    creator = _make_agent(db_session, project_id=project_id, name="creator")
    exp = _make_experiment(
        db_session,
        project_id=project_id,
        creator_id=creator.id,
        phase=ExperimentPhase.draft,
    )

    actions, blocked_on = compute_experiment_capabilities(
        db_session,
        exp,
        creator,
        phase_whitelist=["result_review"],
    )
    assert actions == []
    assert blocked_on == HIDDEN_FOR_CURRENT_PERSONA


# ---------------------------------------------------------------------------
# (5d) experiment_summary_for_actor propagates hidden_for_current_persona
# ---------------------------------------------------------------------------


def test_summary_marks_hidden_for_current_persona_when_excluded(
    db_session: Session, project: dict
) -> None:
    """Fold flows through ``experiment_summary_for_actor``:
    ``hidden_for_current_persona`` True, actions empty, blocked_on ==
    ``hidden_for_current_persona``."""
    project_id = uuid.UUID(project["id"])
    creator = _make_agent(db_session, project_id=project_id, name="creator")
    exp = _make_experiment(
        db_session,
        project_id=project_id,
        creator_id=creator.id,
        phase=ExperimentPhase.draft,
    )

    summary = experiment_summary_for_actor(
        db_session,
        exp,
        creator,
        phase_whitelist=[ExperimentPhase.result_review],
    )
    assert summary.actions == []
    assert summary.blocked_on == HIDDEN_FOR_CURRENT_PERSONA
    assert summary.hidden_for_current_persona is True


def test_summary_hidden_false_when_no_whitelist(
    db_session: Session, project: dict
) -> None:
    """Without a whitelist, the partition flag stays False — even
    though the action set may be empty for other reasons
    (e.g. awaiting_non_creator_review)."""
    project_id = uuid.UUID(project["id"])
    creator = _make_agent(db_session, project_id=project_id, name="creator")
    exp = _make_experiment(
        db_session,
        project_id=project_id,
        creator_id=creator.id,
        phase=ExperimentPhase.review,
    )

    summary = experiment_summary_for_actor(db_session, exp, creator)
    assert summary.hidden_for_current_persona is False
    # actions are still empty for a different reason (no non-creator
    # review yet) — but blocked_on tells the story.
    assert summary.actions == []
    assert summary.blocked_on == "awaiting_non_creator_review"


def test_summary_hidden_false_when_phase_in_whitelist(
    db_session: Session, project: dict
) -> None:
    """Whitelist includes the current phase → ``hidden_for_current_persona``
    is False even when actions happen to be empty for other reasons."""
    project_id = uuid.UUID(project["id"])
    creator = _make_agent(db_session, project_id=project_id, name="creator")
    exp = _make_experiment(
        db_session,
        project_id=project_id,
        creator_id=creator.id,
        phase=ExperimentPhase.draft,
    )

    summary = experiment_summary_for_actor(
        db_session,
        exp,
        creator,
        phase_whitelist=[ExperimentPhase.draft],
    )
    assert summary.hidden_for_current_persona is False
    assert summary.actions == ["submit_for_review"]
    assert summary.blocked_on == "none"


def test_summary_hidden_field_present_on_default(
    db_session: Session, project: dict
) -> None:
    """Sanity: the field defaults to False on a fresh summary — proves
    the schema default does not break pre-existing callers."""
    project_id = uuid.UUID(project["id"])
    creator = _make_agent(db_session, project_id=project_id, name="creator")
    exp = _make_experiment(
        db_session,
        project_id=project_id,
        creator_id=creator.id,
        phase=ExperimentPhase.approved,
    )

    summary = experiment_summary_for_actor(db_session, exp, creator)
    assert hasattr(summary, "hidden_for_current_persona")
    assert summary.hidden_for_current_persona is False
    # Creator in approved → start action available, no fold.
    assert summary.actions == ["start"]
    assert summary.blocked_on == "none"
