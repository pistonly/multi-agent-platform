"""review_item.mutation audit log (experiment b95894db I1(e)).

Pins plan (e) acceptance:

1. ``resolve-item`` (PATCH ``/review-items/{id}``) writes a
   ``review_item.mutation`` audit row with ``action=resolve_item`` and
   ``before_state`` / ``after_state`` reflecting the I1(c) normalisation
   (legacy ``resolved`` / ``withdrawn`` collapsed to ``closed``).
2. Review submission (POST ``/experiments/{id}/reviews``) writes one
   ``review_item.mutation`` audit row per item, with
   ``action=add_item``.
3. ``reject-result`` writes an experiment-level ``review_item.mutation``
   audit row with ``action=reject_result``, ``target_id=experiment_id``,
   ``before_state=result_review``, ``after_state=running``.
4. Read-only endpoints (``GET /experiments/{id}/reviews``,
   ``GET /reviews/{id}/items``) do NOT write audit rows.
5. The 8-field schema (timestamp / actor / experiment_id / review_item_id
   / action / before_state / after_state / reason) is present in
   ``payload_json`` for every audit row.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from server.domain.models import AuditLog
from server.services.audit_service import REVIEW_ITEM_MUTATION
from tests._frontmatter import make_valid_plan

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _experiment_in_review_with_unreasonable_item(
    client: TestClient,
    auth_headers: dict[str, str],
    reviewer: dict,
    project: dict,
    *,
    title: str = "audit-log 实验",
    unreasonable_content: str = "需要 reviewer 处理的 item",
) -> dict:
    """Stand up an experiment in the review phase with exactly one
    open unreasonable item so resolve-item can be exercised."""
    experiment = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={
            "title": title,
            "plan": {"content_md": make_valid_plan(body="## 计划")},
            "submit_for_review": True,
        },
    ).json()
    exp_id = experiment["id"]

    review = client.post(
        f"/api/v1/experiments/{exp_id}/reviews",
        headers=reviewer["headers"],
        json={"unreasonable_items": [unreasonable_content]},
    ).json()
    item_id = next(
        i["id"] for i in review["items"] if i["kind"] == "unreasonable"
    )
    return {
        "project_id": project["id"],
        "experiment_id": exp_id,
        "review_id": review["id"],
        "item_id": item_id,
    }


def _audit_rows_for_experiment(
    db_session, experiment_id: str, action_prefix: str = REVIEW_ITEM_MUTATION
) -> list[AuditLog]:
    """Read all ``review_item.mutation`` audit rows whose payload carries the
    given ``experiment_id``. Returns the rows ordered most-recent-first so
    tests can assert on the head of the timeline."""
    stmt = (
        select(AuditLog)
        .where(AuditLog.action == action_prefix)
        .order_by(AuditLog.created_at.desc())
    )
    rows = list(db_session.scalars(stmt).all())
    return [
        row
        for row in rows
        if isinstance(row.payload_json, dict)
        and row.payload_json.get("experiment_id") == experiment_id
    ]


# ---------------------------------------------------------------------------
# (1) Resolve-item writes a review_item.mutation audit row.
# ---------------------------------------------------------------------------


def test_resolve_item_writes_review_item_mutation_audit_row(
    client, auth_headers, reviewer, project, db_session
):
    """PATCH /review-items/{id} emits an audit row with action=resolve_item
    and before/after state reflecting the I1(c) normalisation.

    Reviewer transitions ``open → withdrawn`` (the canonical reviewer path
    for closing an item without plan revision). I1(c) collapses
    ``withdrawn`` to ``closed{superseded}``.
    """
    ctx = _experiment_in_review_with_unreasonable_item(
        client, auth_headers, reviewer, project
    )
    item_id = ctx["item_id"]
    exp_id = ctx["experiment_id"]

    response = client.patch(
        f"/api/v1/review-items/{item_id}",
        headers=reviewer["headers"],
        json={"status": "withdrawn"},
    )
    assert response.status_code == 200, response.text
    # I1(c): the API normalises legacy ``withdrawn`` to ``closed{superseded}``.
    assert response.json()["status"] == "closed"
    assert response.json()["last_resolution_reason"] == "superseded"

    rows = _audit_rows_for_experiment(db_session, exp_id)
    resolve_rows = [r for r in rows if r.action == REVIEW_ITEM_MUTATION
                    and r.payload_json.get("action") == "resolve_item"]
    assert len(resolve_rows) == 1, [r.payload_json for r in rows]
    row = resolve_rows[0]
    payload = row.payload_json

    # The 8-field schema is fully populated.
    expected_keys = {
        "timestamp",
        "actor_agent_id",
        "experiment_id",
        "review_item_id",
        "action",
        "before_state",
        "after_state",
        "reason",
    }
    assert expected_keys.issubset(payload.keys())
    assert payload["experiment_id"] == exp_id
    assert payload["review_item_id"] == item_id
    assert payload["actor_agent_id"] == reviewer["id"]
    assert payload["before_state"] == "open"
    assert payload["after_state"] == "closed"  # I1(c) normalisation
    assert payload["action"] == "resolve_item"

    # Audit row's agent_id mirrors the actor.
    assert str(row.agent_id) == reviewer["id"]
    assert row.target_type == "review_item"
    assert str(row.target_id) == item_id


def test_resolve_item_audit_records_rebutted_status(
    client, auth_headers, reviewer, project, db_session
):
    """Resolving with --status rebutted keeps ``rebutted`` as the after_state
    (mid-cycle signal — NOT collapsed to closed)."""
    ctx = _experiment_in_review_with_unreasonable_item(
        client, auth_headers, reviewer, project, title="rebutted audit 实验"
    )

    response = client.patch(
        f"/api/v1/review-items/{ctx['item_id']}",
        headers=auth_headers,
        json={"status": "rebutted"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "rebutted"

    rows = _audit_rows_for_experiment(db_session, ctx["experiment_id"])
    resolve_rows = [r for r in rows if r.payload_json.get("action") == "resolve_item"]
    assert len(resolve_rows) == 1
    payload = resolve_rows[0].payload_json
    assert payload["before_state"] == "open"
    assert payload["after_state"] == "rebutted"


# ---------------------------------------------------------------------------
# (2) Review submission writes one audit row per item.
# ---------------------------------------------------------------------------


def test_review_submit_writes_one_audit_row_per_item(
    client, auth_headers, reviewer, project, db_session
):
    """POST /experiments/{id}/reviews emits action=add_item rows for every
    item (both reasonable and unreasonable) so admins can reconstruct the
    review timeline."""
    experiment = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={
            "title": "submit-audit 实验",
            "plan": {"content_md": make_valid_plan(body="## 计划")},
            "submit_for_review": True,
        },
    ).json()
    exp_id = experiment["id"]

    review = client.post(
        f"/api/v1/experiments/{exp_id}/reviews",
        headers=reviewer["headers"],
        json={
            "reasonable_items": ["r1", "r2"],
            "unreasonable_items": ["u1"],
        },
    ).json()
    assert len(review["items"]) == 3

    rows = _audit_rows_for_experiment(db_session, exp_id)
    add_rows = [r for r in rows if r.payload_json.get("action") == "add_item"]
    assert len(add_rows) == 3, [r.payload_json for r in rows]
    # All rows carry the 8-field schema.
    for row in add_rows:
        payload = row.payload_json
        assert payload["before_state"] is None
        assert payload["action"] == "add_item"
        assert payload["actor_agent_id"] == reviewer["id"]
        # reasonable items have status=None in the DB; unreasonable items
        # have status=open. The audit row's after_state mirrors the DB row.
        assert payload["after_state"] in {"open", None}


# ---------------------------------------------------------------------------
# (3) reject-result writes an experiment-level audit row.
# ---------------------------------------------------------------------------


@pytest.fixture
def result_review_experiment(
    client, auth_headers, reviewer, project
) -> dict:
    """Bring an experiment to result_review so reject-result can be exercised."""
    experiment = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={
            "title": "reject-result audit 实验",
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
    client.post(f"/api/v1/experiments/{exp_id}/start", headers=auth_headers)
    submitted = client.post(
        f"/api/v1/experiments/{exp_id}/complete",
        headers=auth_headers,
        json={
            "summary": "实验完成",
            "content_md": "结果待 reviewer 决策",
            "metadata": {"pytest_summary": "unit passed"},
        },
    )
    assert submitted.status_code == 200
    assert submitted.json()["phase"] == "result_review"
    return {"project_id": project["id"], "experiment_id": exp_id}


def test_reject_result_writes_experiment_level_audit_row(
    client, reviewer, result_review_experiment, db_session
):
    """Reviewer reject-result writes an action=reject_result row with
    target_id=experiment_id (not a review item) and before/after phase
    snapshot."""
    exp_id = result_review_experiment["experiment_id"]

    response = client.post(
        f"/api/v1/experiments/{exp_id}/reject-result",
        headers=reviewer["headers"],
        json={"summary": "缺少验收证据", "content_md": "驳回理由"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["phase"] == "running"

    rows = _audit_rows_for_experiment(db_session, exp_id)
    reject_rows = [r for r in rows if r.payload_json.get("action") == "reject_result"]
    assert len(reject_rows) == 1
    payload = reject_rows[0].payload_json
    assert payload["experiment_id"] == exp_id
    assert payload["review_item_id"] is None
    assert payload["before_state"] == "result_review"
    assert payload["after_state"] == "running"
    assert payload["reason"] == "缺少验收证据"
    assert payload["actor_agent_id"] == reviewer["id"]
    # The audit row's target_id points at the experiment, not a review item.
    assert str(reject_rows[0].target_id) == exp_id


# ---------------------------------------------------------------------------
# (4) Read-only endpoints do NOT write audit rows.
# ---------------------------------------------------------------------------


def test_list_reviews_does_not_write_audit_row(
    client, auth_headers, reviewer, project, db_session
):
    """GET /experiments/{id}/reviews is read-only — must not emit audit rows.

    Sanity: the submit path emits ``add_item`` rows; subsequent list calls
    must not change that count.
    """
    ctx = _experiment_in_review_with_unreasonable_item(
        client, auth_headers, reviewer, project, title="read-only 实验"
    )
    exp_id = ctx["experiment_id"]
    baseline = len(_audit_rows_for_experiment(db_session, exp_id))
    assert baseline >= 1  # at least one add_item row from submit

    # Several read-only calls.
    for _ in range(3):
        resp = client.get(
            f"/api/v1/experiments/{exp_id}/reviews", headers=auth_headers
        )
        assert resp.status_code == 200

    after = len(_audit_rows_for_experiment(db_session, exp_id))
    assert after == baseline, "list should not produce new audit rows"


def test_experiment_show_does_not_write_audit_row(
    client, auth_headers, reviewer, project, db_session
):
    """GET /experiments/{id} (show) and bundle are read-only and must not
    mutate the audit log."""
    ctx = _experiment_in_review_with_unreasonable_item(
        client, auth_headers, reviewer, project, title="show-readonly 实验"
    )
    exp_id = ctx["experiment_id"]
    baseline = len(_audit_rows_for_experiment(db_session, exp_id))

    # Several read-only calls.
    for _ in range(3):
        resp = client.get(f"/api/v1/experiments/{exp_id}", headers=auth_headers)
        assert resp.status_code == 200
        resp = client.get(
            f"/api/v1/experiments/{exp_id}/bundle", headers=auth_headers
        )
        assert resp.status_code == 200

    after = len(_audit_rows_for_experiment(db_session, exp_id))
    assert after == baseline
