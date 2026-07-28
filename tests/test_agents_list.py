"""Tests for GET /api/v1/agents — listing visible agents."""

from __future__ import annotations


def _create_agent(client, admin_headers, project, name: str) -> tuple[str, str]:
    response = client.post(
        "/api/v1/agents",
        headers=admin_headers,
        json={"name": name, "role": "agent", "project_key": project["project_key"]},
    )
    assert response.status_code == 201, response.text
    data = response.json()
    return data["id"], data["api_token"]


def test_list_agents_requires_auth(client):
    response = client.get("/api/v1/agents")
    assert response.status_code == 401


def test_admin_lists_all_agents(client, admin_headers, project):
    host_id, _ = _create_agent(client, admin_headers, project, "multi-agent-platform-host")
    participant_id, _ = _create_agent(client, admin_headers, project, "multi-agent-platform-participant")
    reviewer_id, _ = _create_agent(client, admin_headers, project, "multi-agent-platform-reviewer")

    response = client.get("/api/v1/agents", headers=admin_headers)
    assert response.status_code == 200
    data = response.json()
    ids = {item["id"] for item in data}
    names = {item["name"] for item in data}
    assert {host_id, participant_id, reviewer_id}.issubset(ids)
    assert {"multi-agent-platform-host", "multi-agent-platform-participant", "multi-agent-platform-reviewer"}.issubset(names)
    # Admin entry should also be visible.
    assert any(item["role"] == "admin" for item in data)


def test_admin_can_filter_by_role(client, admin_headers, project):
    _create_agent(client, admin_headers, project, "alpha-agent")
    _create_agent(client, admin_headers, project, "beta-agent")

    response = client.get("/api/v1/agents", headers=admin_headers, params={"role": "admin"})
    assert response.status_code == 200
    assert all(item["role"] == "admin" for item in response.json())
    assert len(response.json()) >= 1


def test_project_agent_sees_project_peers_and_admins(client, admin_headers, project):
    host_id, token = _create_agent(client, admin_headers, project, "host-persona")
    other = client.post(
        "/api/v1/projects",
        headers=admin_headers,
        json={
            "project_key": "other-project",
            "name": "Other",
            "workspace_path": "/tmp/other-project",
        },
    )
    assert other.status_code == 201, other.text
    other_project = other.json()
    _create_agent(client, admin_headers, other_project, "outsider-agent")

    headers = {"Authorization": f"Bearer {token}"}

    response = client.get("/api/v1/agents", headers=headers)
    assert response.status_code == 200
    data = response.json()
    ids = {item["id"] for item in data}
    assert host_id in ids
    # Admins should be visible
    assert any(item["role"] == "admin" for item in data)
    # Outsiders should NOT be visible
    outsider_names = {item["name"] for item in data}
    assert "outsider-agent" not in outsider_names


def test_project_agent_can_filter_by_project(client, admin_headers, project):
    host_id, token = _create_agent(client, admin_headers, project, "filtered-host")

    headers = {"Authorization": f"Bearer {token}"}

    response = client.get(
        "/api/v1/agents",
        headers=headers,
        params={"project_id": project["id"]},
    )
    assert response.status_code == 200
    data = response.json()
    assert all(item["project_id"] in (project["id"], None) for item in data)
    assert any(item["id"] == host_id for item in data)
