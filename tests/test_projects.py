def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_register_agent_and_me(client, auth_headers, project):
    response = client.get("/api/v1/agents/me", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "test-agent"
    assert body["project_id"] == project["id"]


def test_project_crud(client, admin_headers):
    create = client.post(
        "/api/v1/projects",
        headers=admin_headers,
        json={
            "project_key": "spectrum-lab",
            "name": "光谱实验",
            "workspace_path": "/tmp/spectrum",
            "description": "测试项目",
        },
    )
    assert create.status_code == 201
    project = create.json()
    project_id = project["id"]
    assert project["project_key"] == "spectrum-lab"
    assert project["current_status_version"] == 1

    listing = client.get("/api/v1/projects", headers=admin_headers)
    assert listing.status_code == 200
    assert len(listing.json()) >= 1

    detail = client.get(f"/api/v1/projects/{project_id}", headers=admin_headers)
    assert detail.status_code == 200
    assert detail.json()["name"] == "光谱实验"

    by_key = client.get("/api/v1/projects/by-key/spectrum-lab", headers=admin_headers)
    assert by_key.status_code == 200
    assert by_key.json()["id"] == project_id

    updated = client.patch(
        f"/api/v1/projects/{project_id}",
        headers=admin_headers,
        json={"description": "更新描述", "archived": True},
    )
    assert updated.status_code == 200
    assert updated.json()["description"] == "更新描述"
    assert updated.json()["archived_at"] is not None


def test_project_status_includes_md(client, admin_headers):
    create = client.post(
        "/api/v1/projects",
        headers=admin_headers,
        json={"project_key": "empty-lab", "name": "空项目", "workspace_path": "/tmp/empty"},
    )
    project_id = create.json()["id"]
    status = client.get(f"/api/v1/projects/{project_id}/status", headers=admin_headers)
    assert status.status_code == 200
    body = status.json()
    assert body["experiment_counts_by_phase"]["draft"] == 0
    assert body["recent_experiments"] == []
    assert body["status_version"] == 1
    assert "Current Status" in body["status_md"]
    assert "进行中的实验" not in body["status_md"]
    assert "快照字段" in body["status_md"]


def test_agent_cannot_create_project(client, auth_headers):
    response = client.post(
        "/api/v1/projects",
        headers=auth_headers,
        json={"project_key": "blocked", "name": "x", "workspace_path": "/tmp/x"},
    )
    assert response.status_code == 403


def test_agent_sees_only_bound_project(client, auth_headers, project, admin_headers):
    other = client.post(
        "/api/v1/projects",
        headers=admin_headers,
        json={"project_key": "other-lab", "name": "Other", "workspace_path": "/tmp/other"},
    ).json()

    listing = client.get("/api/v1/projects", headers=auth_headers)
    assert listing.status_code == 200
    ids = {item["id"] for item in listing.json()}
    assert project["id"] in ids
    assert other["id"] not in ids

    forbidden = client.get(f"/api/v1/projects/{other['id']}", headers=auth_headers)
    assert forbidden.status_code == 403


def test_register_agent_requires_project(client, admin_headers, project):
    missing = client.post(
        "/api/v1/agents",
        headers=admin_headers,
        params={"name": "no-project-agent", "role": "agent"},
    )
    assert missing.status_code == 400

    ok = client.post(
        "/api/v1/agents",
        headers=admin_headers,
        params={"name": "bound-agent", "role": "agent", "project_key": project["project_key"]},
    )
    assert ok.status_code == 201
    assert ok.json()["project_id"] == project["id"]


def test_requires_auth(client):
    response = client.get("/api/v1/projects")
    assert response.status_code == 401
