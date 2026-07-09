"""Tests for cleanup experiment (f12a5638) PR5 — register_agent body model.

Pins topic 1b86ac96 P2 #5:

1. ``POST /api/v1/agents`` takes a JSON body (``AgentCreate``), not query
   params.
2. Duplicate agent name → 409 ConflictError (was HTTPException 409).
3. Missing project for role=agent → 400 BadRequestError (was
   HTTPException 400).
4. project_id pointing at a missing project → 404 NotFoundError from
   svc.get_project (was HTTPException 404).
5. Pydantic body validation rejects shape-level errors with 422.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _register(client: TestClient, headers: dict[str, str], body: dict) -> object:  # noqa: ANN401
    return client.post("/api/v1/agents", headers=headers, json=body)


def test_register_agent_accepts_json_body(client, admin_headers, project):
    """Body-driven creation returns 201 + agent + api_token."""
    resp = _register(
        client,
        admin_headers,
        {
            "name": "body-model-agent",
            "role": "agent",
            "project_key": project["project_key"],
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "body-model-agent"
    assert body["project_id"] == project["id"]
    assert "api_token" in body


def test_register_agent_duplicate_name_returns_409(client, admin_headers, project):
    _register(
        client,
        admin_headers,
        {
            "name": "duplicate-name",
            "role": "agent",
            "project_key": project["project_key"],
        },
    )
    dup = _register(
        client,
        admin_headers,
        {
            "name": "duplicate-name",
            "role": "agent",
            "project_key": project["project_key"],
        },
    )
    assert dup.status_code == 409, dup.text
    assert "already exists" in dup.json()["detail"].lower()


def test_register_agent_missing_project_returns_400(client, admin_headers):
    """role=agent with neither project_id nor project_key → 400 BadRequestError."""
    resp = _register(
        client,
        admin_headers,
        {"name": "no-project", "role": "agent"},
    )
    assert resp.status_code == 400, resp.text
    detail = resp.json()["detail"].lower()
    assert "project_id or project_key" in detail


def test_register_agent_missing_project_id_returns_404(client, admin_headers):
    """project_id that does not exist → 404 NotFoundError from svc.get_project."""
    import uuid

    resp = _register(
        client,
        admin_headers,
        {
            "name": "missing-proj-agent",
            "role": "agent",
            "project_id": str(uuid.uuid4()),
        },
    )
    assert resp.status_code == 404, resp.text


def test_register_agent_invalid_role_returns_422(client, admin_headers):
    """Body shape errors map to pydantic 422 — not 400."""
    resp = _register(
        client,
        admin_headers,
        {
            "name": "bad-role-agent",
            "role": "not-a-real-role",
            "project_key": "k",
        },
    )
    assert resp.status_code == 422


def test_register_agent_missing_name_returns_422(client, admin_headers, project):
    resp = _register(
        client,
        admin_headers,
        {
            "role": "agent",
            "project_key": project["project_key"],
        },
    )
    assert resp.status_code == 422


def test_register_agent_admin_role_no_project_required(client, admin_headers):
    """role=admin can omit project — its project_id stays None."""
    resp = _register(
        client,
        admin_headers,
        {"name": "second-admin", "role": "admin"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["role"] == "admin"
    assert resp.json()["project_id"] is None
