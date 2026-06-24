def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_register_agent_and_me(client, auth_headers):
    response = client.get("/api/v1/agents/me", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["name"] == "test-agent"


def test_project_crud(client, auth_headers):
    create = client.post(
        "/api/v1/projects",
        headers=auth_headers,
        json={
            "name": "光谱实验",
            "workspace_path": "/tmp/spectrum",
            "description": "测试项目",
        },
    )
    assert create.status_code == 201
    project = create.json()
    project_id = project["id"]

    listing = client.get("/api/v1/projects", headers=auth_headers)
    assert listing.status_code == 200
    assert len(listing.json()) == 1

    detail = client.get(f"/api/v1/projects/{project_id}", headers=auth_headers)
    assert detail.status_code == 200
    assert detail.json()["name"] == "光谱实验"

    updated = client.patch(
        f"/api/v1/projects/{project_id}",
        headers=auth_headers,
        json={"description": "更新描述", "archived": True},
    )
    assert updated.status_code == 200
    assert updated.json()["description"] == "更新描述"
    assert updated.json()["archived_at"] is not None

    archived_list = client.get("/api/v1/projects", headers=auth_headers)
    assert archived_list.status_code == 200
    assert len(archived_list.json()) == 0

    include_archived = client.get("/api/v1/projects?include_archived=true", headers=auth_headers)
    assert include_archived.status_code == 200
    assert len(include_archived.json()) == 1


def test_project_status_empty(client, auth_headers):
    create = client.post(
        "/api/v1/projects",
        headers=auth_headers,
        json={"name": "空项目", "workspace_path": "/tmp/empty"},
    )
    project_id = create.json()["id"]
    status = client.get(f"/api/v1/projects/{project_id}/status", headers=auth_headers)
    assert status.status_code == 200
    body = status.json()
    assert body["experiment_counts_by_phase"]["draft"] == 0
    assert body["recent_experiments"] == []


def test_requires_auth(client):
    response = client.get("/api/v1/projects")
    assert response.status_code == 401
