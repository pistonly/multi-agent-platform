"""Tests for cleanup experiment (f12a5638) Exp B — unified ``limit`` query param.

The list experiments endpoint accepts BOTH ``limit`` (preferred) and
``page_size`` (deprecated alias). Rules:

1. When neither is sent, default is 50 (was 100).
2. When ``limit`` is sent, it is used and NO Deprecation header is set
   (even if ``page_size`` was also sent, limit wins).
3. When ONLY ``page_size`` is sent, it is used AND response gets
   ``Deprecation: true`` + ``Sunset: v0.12`` + ``Link: <...>; rel="successor-version"``
   so clients migrate before v0.12.

Out-of-bounds ``limit`` (ge=1, le=100) returns 422.
"""

from __future__ import annotations

from tests._frontmatter import make_valid_plan


def test_default_limit_is_50(client, auth_headers, project):
    """Default (no limit, no page_size) now returns at most 50 items.

    Pre-change the default was 100. We seed 60 experiments so that the
    new 50-cap clips the response — verifying the default actually moved.
    """
    for i in range(60):
        client.post(
            f"/api/v1/projects/{project['id']}/experiments",
            headers=auth_headers,
            json={
                "title": f"实验 {i}",
                "plan": {"content_md": make_valid_plan(body="p")},
            },
        )

    resp = client.get(
        f"/api/v1/projects/{project['id']}/experiments", headers=auth_headers
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 50, f"default page_size must be 50, got {len(body)}"
    assert resp.headers["X-Total-Count"] == "60"
    # No deprecation header — caller used neither param.
    assert "Deprecation" not in resp.headers


def test_limit_param_works(client, auth_headers, project):
    """``limit=10`` caps the response at 10 items, no Deprecation header."""
    for i in range(15):
        client.post(
            f"/api/v1/projects/{project['id']}/experiments",
            headers=auth_headers,
            json={
                "title": f"实验 {i}",
                "plan": {"content_md": make_valid_plan(body="p")},
            },
        )

    resp = client.get(
        f"/api/v1/projects/{project['id']}/experiments?limit=10", headers=auth_headers
    )
    assert resp.status_code == 200
    assert len(resp.json()) == 10
    assert resp.headers["X-Total-Count"] == "15"
    assert "Deprecation" not in resp.headers


def test_page_size_emits_deprecation_header(client, auth_headers, project):
    """``page_size=10`` still works (back-compat) but emits Deprecation
    + Sunset headers so clients migrate."""
    for i in range(15):
        client.post(
            f"/api/v1/projects/{project['id']}/experiments",
            headers=auth_headers,
            json={
                "title": f"实验 {i}",
                "plan": {"content_md": make_valid_plan(body="p")},
            },
        )

    resp = client.get(
        f"/api/v1/projects/{project['id']}/experiments?page_size=10",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert len(resp.json()) == 10
    assert resp.headers["Deprecation"] == "true"
    assert resp.headers["Sunset"] == "v0.12"
    # Link header should advertise the successor (?limit=<page_size>)
    assert "successor-version" in resp.headers.get("Link", "")


def test_limit_wins_over_page_size(client, auth_headers, project):
    """When both ``limit`` and ``page_size`` are sent, ``limit`` wins and
    NO Deprecation header is set (since the deprecated alias was not
    actually used)."""
    for i in range(15):
        client.post(
            f"/api/v1/projects/{project['id']}/experiments",
            headers=auth_headers,
            json={
                "title": f"实验 {i}",
                "plan": {"content_md": make_valid_plan(body="p")},
            },
        )

    resp = client.get(
        f"/api/v1/projects/{project['id']}/experiments?limit=5&page_size=99",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert len(resp.json()) == 5, "limit should win over page_size"
    assert "Deprecation" not in resp.headers


def test_limit_above_max_rejected(client, auth_headers, project):
    """``limit=200`` violates ``le=100`` → 422."""
    resp = client.get(
        f"/api/v1/projects/{project['id']}/experiments?limit=200",
        headers=auth_headers,
    )
    assert resp.status_code == 422


def test_limit_below_min_rejected(client, auth_headers, project):
    """``limit=0`` violates ``ge=1`` → 422."""
    resp = client.get(
        f"/api/v1/projects/{project['id']}/experiments?limit=0",
        headers=auth_headers,
    )
    assert resp.status_code == 422
