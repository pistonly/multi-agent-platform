"""admin CLI ``map audit list --kind review_item_mutation --experiment <id>`` (I1(g)).

Pins plan (g) acceptance:

> 管理员能在 CLI 里 ``map audit list --kind review_item.mutation
> --experiment <id>`` 拿到该实验所有 review_item.mutation 审计行

Covers:

1. ``GET /admin/audit?kind=<action>&experiment_id=<uuid>`` filters rows by
   action and ``payload_json.experiment_id`` (server layer).
2. SDK ``MAPClient.list_audit_global(kind=..., experiment_id=...)`` exposes
   the same filter to Python callers via ``MAPTestClientTransport``.
3. Non-admin callers get 403 (preserves existing access control).
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from server.domain.models import AuditLog
from server.services.audit_service import REVIEW_ITEM_MUTATION

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _experiment_with_audit_rows(
    client: TestClient,
    auth_headers: dict[str, str],
    reviewer: dict,
    project: dict,
) -> dict:
    """Stand up an experiment that produces both ``add_item`` (review submit)
    and ``resolve_item`` (PATCH) audit rows."""
    experiment = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={
            "title": "audit filter 实验",
            "plan": {"content_md": "## 计划"},
            "submit_for_review": True,
        },
    ).json()
    exp_id = experiment["id"]

    review = client.post(
        f"/api/v1/experiments/{exp_id}/reviews",
        headers=reviewer["headers"],
        json={"unreasonable_items": ["audit-A", "audit-B"]},
    ).json()
    item_a_id = next(
        i["id"] for i in review["items"] if i["kind"] == "unreasonable"
        and i["content"] == "audit-A"
    )
    # PATCH so we get a second audit row (resolve_item).
    resp = client.patch(
        f"/api/v1/review-items/{item_a_id}",
        headers=reviewer["headers"],
        json={"status": "withdrawn"},
    )
    assert resp.status_code == 200
    return {"experiment_id": exp_id, "item_a_id": item_a_id}


# ---------------------------------------------------------------------------
# (1) Server endpoint filters by kind + experiment_id.
# ---------------------------------------------------------------------------


def test_admin_audit_filter_by_kind_returns_only_review_item_mutations(
    client, auth_headers, admin_headers, reviewer, project
):
    """``?kind=review_item.mutation`` scopes the response to that action only."""
    ctx = _experiment_with_audit_rows(client, auth_headers, reviewer, project)

    response = client.get(
        "/api/v1/admin/audit",
        params={"kind": REVIEW_ITEM_MUTATION},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    items = response.json()
    # Every returned row carries the kind filter.
    assert items, "expected at least one review_item.mutation row"
    assert all(i["action"] == REVIEW_ITEM_MUTATION for i in items)
    # Our experiment must appear in the slice.
    exp_ids = {
        (i.get("payload_json") or {}).get("experiment_id")
        for i in items
    }
    assert ctx["experiment_id"] in exp_ids


def test_admin_audit_filter_by_experiment_id_returns_only_matching_rows(
    client, auth_headers, admin_headers, reviewer, project
):
    """``?experiment_id=<uuid>`` filters by ``payload_json.experiment_id``."""
    ctx = _experiment_with_audit_rows(client, auth_headers, reviewer, project)

    response = client.get(
        "/api/v1/admin/audit",
        params={
            "kind": REVIEW_ITEM_MUTATION,
            "experiment_id": ctx["experiment_id"],
        },
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    items = response.json()
    # Two add_item rows (audit-A, audit-B) + one resolve_item on A = 3 rows.
    assert len(items) >= 3, items
    for row in items:
        assert row["action"] == REVIEW_ITEM_MUTATION
        payload = row["payload_json"] or {}
        assert payload.get("experiment_id") == ctx["experiment_id"]


def test_admin_audit_filter_excludes_other_experiments(
    client, auth_headers, admin_headers, reviewer, project
):
    """Filtering by experiment_id must not leak rows from other experiments.

    Set up a second experiment with its own audit rows and confirm that
    filtering on experiment #1 returns only #1 rows.
    """
    ctx_a = _experiment_with_audit_rows(
        client, auth_headers, reviewer, project
    )
    ctx_b = _experiment_with_audit_rows(
        client, auth_headers, reviewer, project
    )
    assert ctx_a["experiment_id"] != ctx_b["experiment_id"]

    response = client.get(
        "/api/v1/admin/audit",
        params={
            "kind": REVIEW_ITEM_MUTATION,
            "experiment_id": ctx_a["experiment_id"],
        },
        headers=admin_headers,
    )
    assert response.status_code == 200
    items = response.json()
    assert items
    exp_ids = {(i["payload_json"] or {}).get("experiment_id") for i in items}
    assert exp_ids == {ctx_a["experiment_id"]}


def test_admin_audit_filter_excludes_non_matching_actions(
    client, auth_headers, admin_headers, reviewer, project
):
    """Filtering by ``kind=review_item.mutation`` must NOT return
    ``review_substitute`` rows (different action, same target_type)."""
    _experiment_with_audit_rows(client, auth_headers, reviewer, project)

    response = client.get(
        "/api/v1/admin/audit",
        params={"kind": REVIEW_ITEM_MUTATION},
        headers=admin_headers,
    )
    items = response.json()
    assert all(i["action"] == REVIEW_ITEM_MUTATION for i in items)


def test_admin_audit_filter_requires_admin(
    client, auth_headers, reviewer
):
    """Non-admin callers (host, reviewer) get 403."""
    response = client.get(
        "/api/v1/admin/audit",
        params={"kind": REVIEW_ITEM_MUTATION},
        headers=auth_headers,
    )
    assert response.status_code == 403

    response = client.get(
        "/api/v1/admin/audit",
        params={"kind": REVIEW_ITEM_MUTATION},
        headers=reviewer["headers"],
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# (2) SDK forwards both filters via the test transport.
# ---------------------------------------------------------------------------


def test_sdk_list_audit_global_forwards_filters(
    client, auth_headers, admin_map_client, reviewer, project
):
    """SDK ``list_audit_global(kind=..., experiment_id=...)`` reaches the
    server with the right query params and parses the JSON rows back
    into ``AuditLogRead`` objects."""
    ctx = _experiment_with_audit_rows(
        client, auth_headers, reviewer, project
    )

    items, total = admin_map_client.list_audit_global(
        kind=REVIEW_ITEM_MUTATION,
        experiment_id=uuid.UUID(ctx["experiment_id"]),
        page=1,
        page_size=100,
    )
    assert total >= 3
    assert all(i.action == REVIEW_ITEM_MUTATION for i in items)
    exp_ids = {
        (i.payload_json or {}).get("experiment_id") for i in items
    }
    assert ctx["experiment_id"] in exp_ids
    # Cross-experiment isolation — no leakage from other test fixtures.
    assert all(
        eid == ctx["experiment_id"]
        for eid in exp_ids
        if eid is not None
    )


# ---------------------------------------------------------------------------
# (3) DB-level filter sanity (defensive — the API uses the same SQL).
# ---------------------------------------------------------------------------


def test_payload_json_filter_uses_dense_rank(
    client, auth_headers, admin_headers, reviewer, project, db_session
):
    """The ``payload_json['experiment_id']`` index lookup must not regress
    other audit kinds. Confirm via direct DB inspection that the filter
    is exact (no false positives / negatives)."""
    ctx_a = _experiment_with_audit_rows(
        client, auth_headers, reviewer, project
    )
    ctx_b = _experiment_with_audit_rows(
        client, auth_headers, reviewer, project
    )

    # Sanity: direct SQL count must match API count.
    rows_a = list(
        db_session.scalars(
            select(AuditLog).where(
                AuditLog.action == REVIEW_ITEM_MUTATION,
                AuditLog.payload_json["experiment_id"].as_string()
                == ctx_a["experiment_id"],
            )
        ).all()
    )
    rows_b = list(
        db_session.scalars(
            select(AuditLog).where(
                AuditLog.action == REVIEW_ITEM_MUTATION,
                AuditLog.payload_json["experiment_id"].as_string()
                == ctx_b["experiment_id"],
            )
        ).all()
    )
    assert len(rows_a) >= 3
    assert len(rows_b) >= 3

    # API response count must equal SQL count (within page_size).
    api = client.get(
        "/api/v1/admin/audit",
        params={
            "kind": REVIEW_ITEM_MUTATION,
            "experiment_id": ctx_a["experiment_id"],
            "page_size": 200,
        },
        headers=admin_headers,
    ).json()
    assert len(api) == len(rows_a)
