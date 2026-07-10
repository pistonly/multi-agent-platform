"""Tests for authz experiment (0e6926fa) PR3 — extend
``ensure_experiment_creator_or_admin`` to non-state-machine mutations.

Topic 2d6d6f3d listed 4 remaining issues after PR1/PR2:

1. PATCH /experiments/{id} — only checked project access, not creator
2. DELETE /experiments/{id} — same
3. lock_service._get didn't filter deleted_at (so soft-deleted
   experiments could still be locked/unlocked)
4. scan_stalled checked ``agent.name == HOST_AGENT_NAME`` (brittle —
   breaks if the host agent is renamed or a second host agent is
   registered)

PR3 fixes all four:

* PATCH/DELETE now call ``ensure_experiment_creator_or_admin``
* ``lock_service._get`` filters ``deleted_at IS NULL``
* scan_stalled uses ``agent.has_capability("system:scan_stalled_locks")``
  or admin instead of name equality

Out of scope (already resolved by PR2):
* record_cross_persona_call — already capability-gated by PR2
"""

from __future__ import annotations

from tests._frontmatter import make_valid_plan

# ────────────────────────── PATCH gating ──────────────────────────


def test_patch_experiment_by_creator_succeeds(client, auth_headers, project):
    """The creator can still PATCH their own experiment."""
    create = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={
            "title": "patch creator test",
            "plan": {"content_md": make_valid_plan(body="p")},
        },
    )
    assert create.status_code == 201, create.text
    exp_id = create.json()["id"]

    resp = client.patch(
        f"/api/v1/experiments/{exp_id}",
        headers=auth_headers,
        json={"title": "renamed by creator"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["title"] == "renamed by creator"


def test_patch_experiment_by_other_project_member_forbidden(
    client, auth_headers, project, reviewer
):
    """A reviewer (not creator, not admin) must get 403 on PATCH."""
    # Create experiment as host
    create = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={
            "title": "patch third-party test",
            "plan": {"content_md": make_valid_plan(body="p")},
        },
    )
    assert create.status_code == 201, create.text
    exp_id = create.json()["id"]

    # Reviewer (different agent, same project) tries to rename
    resp = client.patch(
        f"/api/v1/experiments/{exp_id}",
        headers=reviewer["headers"],
        json={"title": "reviewer rename attempt"},
    )
    assert resp.status_code == 403, (
        f"non-creator reviewer must not be able to PATCH; got {resp.status_code} {resp.text}"
    )


# ────────────────────────── DELETE gating ──────────────────────────


def test_delete_experiment_by_creator_succeeds(client, auth_headers, project):
    """The creator can soft-delete their own experiment."""
    create = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={
            "title": "delete creator test",
            "plan": {"content_md": make_valid_plan(body="p")},
        },
    )
    assert create.status_code == 201, create.text
    exp_id = create.json()["id"]

    resp = client.delete(
        f"/api/v1/experiments/{exp_id}", headers=auth_headers
    )
    assert resp.status_code == 204, resp.text


def test_delete_experiment_by_other_project_member_forbidden(
    client, auth_headers, project, reviewer
):
    """A reviewer must not be able to delete an experiment they don't own."""
    create = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={
            "title": "delete third-party test",
            "plan": {"content_md": make_valid_plan(body="p")},
        },
    )
    assert create.status_code == 201, create.text
    exp_id = create.json()["id"]

    resp = client.delete(
        f"/api/v1/experiments/{exp_id}", headers=reviewer["headers"]
    )
    assert resp.status_code == 403, (
        f"non-creator reviewer must not be able to DELETE; got {resp.status_code} {resp.text}"
    )


# ────────────────────────── scan_stalled capability check ──────────────────────────


def test_scan_stalled_by_admin_succeeds(client, admin_headers):
    """An admin can always run scan_stalled (admin short-circuits the capability check)."""
    resp = client.post(
        "/api/v1/experiments/lock/scan-stalled", headers=admin_headers
    )
    assert resp.status_code == 200, resp.text


def test_scan_stalled_by_reviewer_forbidden(client, auth_headers, reviewer):
    """A reviewer (no scan_stalled capability) gets 403."""
    resp = client.post(
        "/api/v1/experiments/lock/scan-stalled", headers=reviewer["headers"]
    )
    assert resp.status_code == 403, (
        f"reviewer without capability must get 403; got {resp.status_code} {resp.text}"
    )


# ────────────────────────── lock_service deleted_at filter ──────────────────────────


def test_lock_acquire_on_soft_deleted_experiment_returns_404(client, auth_headers, project):
    """After soft-delete, lock/acquire must surface 404 (not silently succeed)."""
    create = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={
            "title": "lock after delete test",
            "plan": {"content_md": make_valid_plan(body="p")},
        },
    )
    assert create.status_code == 201, create.text
    exp_id = create.json()["id"]

    # Soft-delete
    del_resp = client.delete(
        f"/api/v1/experiments/{exp_id}", headers=auth_headers
    )
    assert del_resp.status_code == 204, del_resp.text

    # Try to acquire lock — must 404 (deleted experiment hidden)
    lock_resp = client.post(
        f"/api/v1/experiments/{exp_id}/lock/acquire",
        headers=auth_headers,
        json={"ttl_seconds": 60},
    )
    assert lock_resp.status_code == 404, (
        f"lock on deleted experiment must 404; got {lock_resp.status_code} {lock_resp.text}"
    )
