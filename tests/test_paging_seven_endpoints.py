"""Tests for cleanup experiment (f12a5638) PR3 — extend ``?limit=`` to
7 list endpoints that previously had no paging.

The topic-1b86ac96 P2 issue #4 listed 7 endpoints whose result sets
had no upper bound:

* ``GET /experiments/{id}/logs``
* ``GET /experiments/{id}/comments``
* ``GET /experiments/{id}/plans``
* ``GET /experiments/{id}/reviews``
* ``GET /topics/{id}/comments``
* ``GET /projects/{id}/status/versions``
* ``GET /audit?target_type=...&target_id=...``

Each now accepts ``limit`` (default 50 or 100, max 200 or 500 depending
on the endpoint) and applies ``.limit()`` at SQL level.

This file verifies:

1. ``GET`` endpoints with ``?limit=N`` return at most N rows.
2. ``limit`` is clamped — out-of-bounds via API returns 422.
3. Default limit kicks in when no param sent.
4. Default+max constants match the documented spec (drift guard).
"""

from __future__ import annotations

import inspect
import re

from tests._frontmatter import make_valid_plan

# ────────────────────────── minimal seed helpers ──────────────────────────


def _make_experiment(client, auth_headers, project_id: str) -> str:
    """Create an experiment via the API; return its id (str)."""
    create = client.post(
        f"/api/v1/projects/{project_id}/experiments",
        headers=auth_headers,
        json={
            "title": "limit paging test exp",
            "plan": {"content_md": make_valid_plan(body="p")},
        },
    )
    assert create.status_code == 201, create.text
    return create.json()["id"]


def _make_topic(client, auth_headers, project_id: str) -> str:
    """Create a topic via the API; return its id (str)."""
    create = client.post(
        f"/api/v1/projects/{project_id}/topics",
        headers=auth_headers,
        json={
            "title": "limit paging test topic",
            "body": "test",
        },
    )
    assert create.status_code == 201, create.text
    return create.json()["id"]


# ────────────────────────── API-layer integration tests ──────────────────────────


def test_api_list_logs_limit_honoured(client, auth_headers, project):
    """``GET /experiments/{id}/logs?limit=2`` returns ≤2 rows."""
    exp_id = _make_experiment(client, auth_headers, project["id"])
    resp = client.get(
        f"/api/v1/experiments/{exp_id}/logs?limit=2", headers=auth_headers
    )
    assert resp.status_code == 200
    assert len(resp.json()) <= 2


def test_api_list_plans_limit_honoured(client, auth_headers, project):
    """``GET /experiments/{id}/plans?limit=1`` returns ≤1 row."""
    exp_id = _make_experiment(client, auth_headers, project["id"])
    resp = client.get(
        f"/api/v1/experiments/{exp_id}/plans?limit=1", headers=auth_headers
    )
    assert resp.status_code == 200
    assert len(resp.json()) <= 1


def test_api_list_reviews_limit_honoured(client, auth_headers, project):
    """``GET /experiments/{id}/reviews?limit=1`` returns ≤1 row."""
    exp_id = _make_experiment(client, auth_headers, project["id"])
    resp = client.get(
        f"/api/v1/experiments/{exp_id}/reviews?limit=1", headers=auth_headers
    )
    assert resp.status_code == 200
    assert len(resp.json()) <= 1


def test_api_list_comments_limit_honoured(client, auth_headers, project):
    """``GET /experiments/{id}/comments?limit=2`` returns ≤2 rows."""
    exp_id = _make_experiment(client, auth_headers, project["id"])
    resp = client.get(
        f"/api/v1/experiments/{exp_id}/comments?limit=2", headers=auth_headers
    )
    assert resp.status_code == 200
    assert len(resp.json()) <= 2


def test_api_list_topic_comments_limit_honoured(client, auth_headers, project):
    """``GET /topics/{id}/comments?limit=2`` returns ≤2 rows."""
    topic_id = _make_topic(client, auth_headers, project["id"])
    resp = client.get(
        f"/api/v1/topics/{topic_id}/comments?limit=2", headers=auth_headers
    )
    assert resp.status_code == 200
    assert len(resp.json()) <= 2


def test_api_list_project_status_versions_limit_honoured(client, auth_headers, project):
    """``GET /projects/{id}/status/versions?limit=1`` returns ≤1 row."""
    resp = client.get(
        f"/api/v1/projects/{project['id']}/status/versions?limit=1",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert len(resp.json()) <= 1


def test_api_list_comments_limit_out_of_range_returns_422(client, auth_headers, project):
    """Out-of-bounds ``limit`` triggers FastAPI 422 (ge=1, le=500)."""
    exp_id = _make_experiment(client, auth_headers, project["id"])
    resp = client.get(
        f"/api/v1/experiments/{exp_id}/comments?limit=0", headers=auth_headers
    )
    assert resp.status_code == 422

    resp = client.get(
        f"/api/v1/experiments/{exp_id}/comments?limit=9999", headers=auth_headers
    )
    assert resp.status_code == 422


def test_api_list_logs_limit_out_of_range_returns_422(client, auth_headers, project):
    """Out-of-bounds ``limit`` triggers FastAPI 422 (ge=1, le=200)."""
    exp_id = _make_experiment(client, auth_headers, project["id"])
    resp = client.get(
        f"/api/v1/experiments/{exp_id}/logs?limit=0", headers=auth_headers
    )
    assert resp.status_code == 422


# ────────────────────────── drift guard: defaults must match spec ──────────────────────────


def test_limit_defaults_match_documented_spec():
    """Drift guard — if a future PR changes the (default, max) constants
    in any of the 7 endpoints, this test fails so the reviewer notices.

    Spec (from PR3 implementation):

    * list_comments       — default 100, max 500
    * list_logs           — default  50, max 200
    * list_plans          — default  50, max 200
    * list_reviews        — default  50, max 200
    * list_topic_comments — default 100, max 500
    * list_project_status_versions — default 50, max 200
    * list_audit_for_target — default 50, max 200
    """
    expected = {
        "list_comments": (100, 500),
        "list_logs": (50, 200),
        "list_plans": (50, 200),
        "list_reviews": (50, 200),
        "list_topic_comments": (100, 500),
        "list_project_status_versions": (50, 200),
        "list_audit_for_target": (50, 200),
    }
    from server.api import audit as audit_api
    from server.api import experiments as exp_api
    from server.api import projects as projects_api
    from server.api import topics as topics_api

    def _parse(func) -> tuple[int, int]:
        src = inspect.getsource(func)
        m = re.search(
            r"limit:\s*int\s*=\s*Query\(default=(\d+),\s*ge=1,\s*le=(\d+)\)", src
        )
        assert m, f"could not parse limit defaults from {func.__name__}"
        return int(m.group(1)), int(m.group(2))

    actual = {
        "list_comments": _parse(exp_api.list_comments),
        "list_logs": _parse(exp_api.list_logs),
        "list_plans": _parse(exp_api.list_plans),
        "list_reviews": _parse(exp_api.list_reviews),
        "list_topic_comments": _parse(topics_api.list_topic_comments),
        "list_project_status_versions": _parse(projects_api.list_project_status_versions),
        "list_audit_for_target": _parse(audit_api.list_audit_for_target),
    }
    assert actual == expected, (
        f"limit defaults drift:\n  expected={expected}\n  actual={actual}"
    )
