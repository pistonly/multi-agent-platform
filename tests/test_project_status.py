def test_admin_revise_project_status(client, admin_headers, project):
    project_id = project["id"]
    revised_md = "# Current Status — test-project\n\n## 当前目标\n\n- 完成图表可视化实验\n"

    response = client.post(
        f"/api/v1/projects/{project_id}/status/revisions",
        headers=admin_headers,
        json={"content_md": revised_md, "change_note": "更新目标"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["version"] == 2
    assert body["content_md"] == revised_md
    assert body["change_note"] == "更新目标"

    status = client.get(f"/api/v1/projects/{project_id}/status", headers=admin_headers)
    assert status.status_code == 200
    status_body = status.json()
    assert status_body["status_version"] == 2
    assert "图表可视化" in status_body["status_md"]


def test_agent_can_revise_own_project_status(client, auth_headers, project):
    revised_md = "# Current Status — test-project\n\n## 当前目标\n\n- host 修订 status_md\n"

    response = client.post(
        f"/api/v1/projects/{project['id']}/status/revisions",
        headers=auth_headers,
        json={"content_md": revised_md, "change_note": "host 同步"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["version"] == 2
    assert body["content_md"] == revised_md

    status = client.get(f"/api/v1/projects/{project['id']}/status", headers=auth_headers)
    assert status.status_code == 200
    assert "host 修订 status_md" in status.json()["status_md"]


def test_agent_cannot_revise_other_project_status(client, auth_headers, admin_headers):
    other = client.post(
        "/api/v1/projects",
        headers=admin_headers,
        json={"project_key": "other-lab", "name": "Other", "workspace_path": "/tmp/other"},
    ).json()

    response = client.post(
        f"/api/v1/projects/{other['id']}/status/revisions",
        headers=auth_headers,
        json={"content_md": "# blocked\n"},
    )
    assert response.status_code == 403


def test_list_and_get_status_versions(client, admin_headers, auth_headers, project):
    project_id = project["id"]
    client.post(
        f"/api/v1/projects/{project_id}/status/revisions",
        headers=admin_headers,
        json={"content_md": "# v2\n", "change_note": "v2"},
    )

    listing = client.get(f"/api/v1/projects/{project_id}/status/versions", headers=auth_headers)
    assert listing.status_code == 200
    versions = listing.json()
    assert len(versions) == 2
    assert versions[0]["version"] == 2
    assert versions[1]["version"] == 1

    detail = client.get(
        f"/api/v1/projects/{project_id}/status/versions/1",
        headers=auth_headers,
    )
    assert detail.status_code == 200
    assert detail.json()["version"] == 1
    assert "Current Status" in detail.json()["content_md"]


def test_status_version_not_found(client, admin_headers, project):
    response = client.get(
        f"/api/v1/projects/{project['id']}/status/versions/999",
        headers=admin_headers,
    )
    assert response.status_code == 404
