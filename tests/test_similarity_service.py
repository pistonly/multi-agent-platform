"""b72d0542 I1.b(2)(b) — service-level tests for the log content similarity
soft validator.

Acceptance cases (from plan):

| Case | previous log body | new log body     | result                       |
|------|-------------------|------------------|------------------------------|
| 1    | (none)            | any              | warnings=[]                  |
| 2    | body_A            | body_A (same)    | warnings=[HIGH_CONTENT_SIMILARITY] score=1.0 |
| 3    | body_A            | body_B (diff)    | warnings=[]                  |

Soft validation invariant: ``valid`` is always True so ``experiment log``
is never blocked.
"""

from __future__ import annotations

import uuid

from map_types.enums import AgentRole, ExperimentPhase
from sqlalchemy.orm import Session

from server.domain.models import Agent, Experiment, ExperimentLog
from server.services.similarity_service import (
    SIMILARITY_MODEL_ID,
    SIMILARITY_THRESHOLD,
    SimilarityValidationResult,
    validate_log_similarity,
)


def _make_agent(
    db: Session, *, project_id: uuid.UUID, name: str, role: AgentRole = AgentRole.agent
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


def _make_experiment(db: Session, *, project_id: uuid.UUID, creator_id: uuid.UUID) -> Experiment:
    exp = Experiment(
        id=uuid.uuid4(),
        project_id=project_id,
        creator_agent_id=creator_id,
        title="similarity test",
        description=None,
        phase=ExperimentPhase.running,
    )
    db.add(exp)
    db.flush()
    return exp


def _make_log(db: Session, *, experiment_id: uuid.UUID, body: str, author_id: uuid.UUID | None = None) -> ExperimentLog:
    log = ExperimentLog(
        id=uuid.uuid4(),
        experiment_id=experiment_id,
        author_agent_id=author_id,
        summary="prev",
        content_md=body,
        log_index=1,
    )
    db.add(log)
    db.flush()
    return log


def test_case_1_no_prior_log_no_warning(db_session: Session, project: dict) -> None:
    """Case 1: no prior log → warnings=[]."""
    project_id = uuid.UUID(project["id"])
    creator = _make_agent(db_session, project_id=project_id, name="creator")
    exp = _make_experiment(db_session, project_id=project_id, creator_id=creator.id)
    result = validate_log_similarity(
        db_session, experiment_id=exp.id, content_md="anything goes"
    )
    assert isinstance(result, SimilarityValidationResult)
    assert result.warnings == []
    assert result.valid is True
    assert result.threshold == SIMILARITY_THRESHOLD
    assert result.model == SIMILARITY_MODEL_ID


def test_case_2_identical_body_emits_warning(db_session: Session, project: dict) -> None:
    """Case 2: new log body equals last log body → HIGH_CONTENT_SIMILARITY."""
    project_id = uuid.UUID(project["id"])
    creator = _make_agent(db_session, project_id=project_id, name="creator")
    exp = _make_experiment(db_session, project_id=project_id, creator_id=creator.id)
    _make_log(db_session, experiment_id=exp.id, body="duplicate body", author_id=creator.id)
    result = validate_log_similarity(
        db_session, experiment_id=exp.id, content_md="duplicate body"
    )
    assert len(result.warnings) == 1
    w = result.warnings[0]
    assert w.code == "HIGH_CONTENT_SIMILARITY"
    assert w.score == 1.0
    assert w.threshold == SIMILARITY_THRESHOLD
    assert w.ref_log_id is not None
    assert result.valid is True


def test_case_3_different_body_no_warning(db_session: Session, project: dict) -> None:
    """Case 3: different body → warnings=[]."""
    project_id = uuid.UUID(project["id"])
    creator = _make_agent(db_session, project_id=project_id, name="creator")
    exp = _make_experiment(db_session, project_id=project_id, creator_id=creator.id)
    _make_log(db_session, experiment_id=exp.id, body="body A", author_id=creator.id)
    result = validate_log_similarity(
        db_session, experiment_id=exp.id, content_md="body B (completely different)"
    )
    assert result.warnings == []
    assert result.valid is True


def test_valid_invariant_always_true(db_session: Session, project: dict) -> None:
    """Soft validation invariant: ``valid`` is True even when warnings fire."""
    project_id = uuid.UUID(project["id"])
    creator = _make_agent(db_session, project_id=project_id, name="creator")
    exp = _make_experiment(db_session, project_id=project_id, creator_id=creator.id)
    _make_log(db_session, experiment_id=exp.id, body="x", author_id=creator.id)
    result = validate_log_similarity(db_session, experiment_id=exp.id, content_md="x")
    assert result.warnings
    assert result.valid is True


def test_threshold_constant() -> None:
    """Threshold exported as a single source of truth (plan: 0.7)."""
    assert SIMILARITY_THRESHOLD == 0.7


def test_model_id_constant() -> None:
    """Model id placeholder is the literal ``placeholder:jaccard-v0`` until
    plan-(f) swaps in ``sentence-transformers/all-MiniLM-L6-v2``.
    """
    assert SIMILARITY_MODEL_ID == "placeholder:jaccard-v0"
