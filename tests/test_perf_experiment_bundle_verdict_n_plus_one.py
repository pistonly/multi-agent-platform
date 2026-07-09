"""perf experiment (193a5074) PR4 Layer 3 — bundle verdict-reasons N+1.

Pins:
* ``get_experiment_bundle`` issues EXACTLY 1 verdict-reasons lookup
  (``SELECT … FROM experiment_logs … WHERE metadata_json IS NOT NULL
  ORDER BY created_at DESC LIMIT 1``), regardless of how many reviews
  the experiment has.
* When the experiment has no reviews, the verdict-reasons lookup is
  skipped entirely.
* The ``review_to_read`` helper honors a precomputed verdict-reasons
  map (the bundle path).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import event

from server.domain.models import (
    Agent,
    AgentRole,
    Experiment,
    Review,
    ReviewItem,
    ReviewItemKind,
)
from tests._frontmatter import make_valid_plan


@pytest.fixture
def verdict_select_counter(engine):
    """Count verdict-reasons lookups (``metadata_json IS NOT NULL`` SELECTs).

    The verdict-reasons path is the one PR4 hoists: it filters on
    ``metadata_json IS NOT NULL`` and orders by ``created_at DESC LIMIT 1``.
    Before PR4, ``get_experiment_bundle`` issued one such SELECT per review.
    """
    counter = {"n": 0}

    def _on_execute(conn, cursor, statement, params, context, executemany):  # noqa: ANN001
        s = statement.lower()
        if not s.lstrip().startswith("select"):
            return
        if "from experiment_logs" not in s:
            return
        if "metadata_json is not null" in s:
            counter["n"] += 1

    event.listen(engine, "before_cursor_execute", _on_execute)
    try:
        yield counter
    finally:
        event.remove(engine, "before_cursor_execute", _on_execute)


def _seed_experiment_with_n_reviews(
    db_session,
    *,
    project_id: uuid.UUID,
    host_agent_id: uuid.UUID,
    n_reviews: int,
) -> str:
    """Insert an experiment + N reviews directly via the ORM.

    Bypasses the API review-submission flow (which enforces "one review
    per reviewer per plan_version") so a single experiment can carry
    arbitrarily many reviews. The perf fix doesn't depend on token /
    role plumbing — synthetic reviewer agents are fine.
    """
    exp = Experiment(
        project_id=project_id,
        creator_agent_id=host_agent_id,
        title=f"bundle-n+1-{uuid.uuid4().hex[:6]}",
        description="perf PR4 fixture",
        phase="review",
        current_plan_version=1,
    )
    db_session.add(exp)
    db_session.flush()

    for i in range(n_reviews):
        reviewer = Agent(
            project_id=project_id,
            name=f"perf-bundle-reviewer-{uuid.uuid4().hex[:6]}",
            role=AgentRole.agent,
            api_token_hash="x" * 64,
            api_token_prefix=f"perf-bnd-{i:02d}",
        )
        db_session.add(reviewer)
        db_session.flush()
        review = Review(
            experiment_id=exp.id,
            reviewer_agent_id=reviewer.id,
            plan_version=1,
        )
        db_session.add(review)
        db_session.flush()
        db_session.add(
            ReviewItem(
                review_id=review.id,
                kind=ReviewItemKind.unreasonable,
                content=f"item {i}",
            )
        )
    db_session.commit()
    db_session.expire_all()
    return str(exp.id)


# ---------------------------------------------------------------------------
# Bundle-level: R reviews on the same experiment → 1 verdict lookup
# ---------------------------------------------------------------------------


def test_get_experiment_bundle_verdict_lookup_is_single_select(
    client, project, admin_headers, db_session, verdict_select_counter
):
    """N reviews on the same experiment → exactly 1 verdict-reasons SELECT.

    Before PR4 the verdict path alone issued N SELECTs for N reviews
    (every ``review_to_read`` re-queried the same latest verdict log).
    """
    admin_row = db_session.query(Agent).filter(Agent.name == "admin-agent").first()
    assert admin_row is not None

    exp_id = _seed_experiment_with_n_reviews(
        db_session,
        project_id=uuid.UUID(project["id"]),
        host_agent_id=admin_row.id,
        n_reviews=4,
    )

    resp = client.get(f"/api/v1/experiments/{exp_id}/bundle", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert len(payload["reviews"]) == 4
    assert verdict_select_counter["n"] == 1, (
        f"expected exactly 1 verdict-reasons SELECT for bundle, "
        f"got {verdict_select_counter['n']}"
    )


def test_get_experiment_bundle_no_reviews_no_verdict_query(
    client, project, admin_headers, db_session, verdict_select_counter
):
    """Empty review list → no verdict-reasons SELECT at all.

    The hoist guards on ``if reviews_orm`` so an experiment with zero
    reviews never hits the verdict lookup.
    """
    resp = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=admin_headers,
        json={
            "title": f"bundle-empty-{uuid.uuid4().hex[:6]}",
            "plan": {"content_md": make_valid_plan(body="p")},
        },
    )
    assert resp.status_code == 201, resp.text
    exp_id = resp.json()["id"]

    resp = client.get(f"/api/v1/experiments/{exp_id}/bundle", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["reviews"] == []
    assert verdict_select_counter["n"] == 0, (
        f"expected no verdict-reasons SELECT when reviews_orm is empty, "
        f"got {verdict_select_counter['n']}"
    )


# ---------------------------------------------------------------------------
# Helper-level: review_to_read honors precomputed verdict_reasons
# ---------------------------------------------------------------------------


def test_review_to_read_precomputed_reasons_skip_lookup(
    db_session, engine, project, admin_headers, verdict_select_counter
):
    """``review_to_read(review, verdict_reasons={...})`` skips the SELECT.

    Builds its own experiment + review so the assertion is deterministic.
    """
    from server.services import review_service

    admin_row = db_session.query(Agent).filter(Agent.name == "admin-agent").first()
    assert admin_row is not None

    exp = Experiment(
        project_id=uuid.UUID(project["id"]),
        creator_agent_id=admin_row.id,
        title=f"helper-n+1-{uuid.uuid4().hex[:6]}",
        description="perf PR4 helper fixture",
        phase="review",
        current_plan_version=1,
    )
    db_session.add(exp)
    db_session.flush()
    review = Review(
        experiment_id=exp.id,
        reviewer_agent_id=admin_row.id,
        plan_version=1,
    )
    db_session.add(review)
    db_session.flush()
    db_session.add(
        ReviewItem(
            review_id=review.id,
            kind=ReviewItemKind.unreasonable,
            content="stub",
        )
    )
    db_session.commit()
    db_session.expire_all()

    out = review_service.review_to_read(db_session, review, verdict_reasons={})

    assert out.id == review.id
    assert verdict_select_counter["n"] == 0, (
        f"review_to_read with precomputed verdict_reasons should skip the SELECT; "
        f"got {verdict_select_counter['n']}"
    )
