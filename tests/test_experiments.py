def test_experiment_crud(client, auth_headers, project):
    create = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={
            "title": "噪声基线实验",
            "description": "测量暗电流",
            "plan": {"content_md": "## 目标\n测量基线", "change_note": "初始版本"},
            "submit_for_review": True,
        },
    )
    assert create.status_code == 201
    experiment = create.json()
    assert experiment["phase"] == "review"
    assert experiment["current_plan_version"] == 1
    experiment_id = experiment["id"]

    listing = client.get(f"/api/v1/projects/{project['id']}/experiments", headers=auth_headers)
    assert listing.status_code == 200
    assert len(listing.json()) == 1

    filtered = client.get(
        f"/api/v1/projects/{project['id']}/experiments?phase=review",
        headers=auth_headers,
    )
    assert filtered.status_code == 200
    assert len(filtered.json()) == 1

    detail = client.get(f"/api/v1/experiments/{experiment_id}", headers=auth_headers)
    assert detail.status_code == 200
    body = detail.json()
    assert body["current_plan"]["content_md"] == "## 目标\n测量基线"
    assert body["plan_version_count"] == 1

    updated = client.patch(
        f"/api/v1/experiments/{experiment_id}",
        headers=auth_headers,
        json={"title": "噪声基线实验 v2"},
    )
    assert updated.status_code == 200
    assert updated.json()["title"] == "噪声基线实验 v2"

    deleted = client.delete(f"/api/v1/experiments/{experiment_id}", headers=auth_headers)
    assert deleted.status_code == 204

    after_delete = client.get(f"/api/v1/experiments/{experiment_id}", headers=auth_headers)
    assert after_delete.status_code == 404


def test_experiment_bundle(client, auth_headers, project):
    create = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={
            "title": "Bundle 测试",
            "plan": {"content_md": "## plan", "change_note": "v1"},
            "submit_for_review": True,
        },
    )
    assert create.status_code == 201
    experiment_id = create.json()["id"]

    bundle = client.get(f"/api/v1/experiments/{experiment_id}/bundle", headers=auth_headers)
    assert bundle.status_code == 200
    body = bundle.json()
    assert body["experiment"]["id"] == experiment_id
    assert len(body["plans"]) == 1
    assert body["plans"][0]["content_md"] == "## plan"
    assert body["reviews"] == []
    assert body["comments"] == []
    assert body["logs"] == []


def test_experiment_draft_phase(client, auth_headers, project):
    create = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={
            "title": "草稿实验",
            "plan": {"content_md": "plan"},
            "submit_for_review": False,
        },
    )
    assert create.status_code == 201
    assert create.json()["phase"] == "draft"


def test_project_status_with_experiments(client, auth_headers, project):
    client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={
            "title": "实验 A",
            "plan": {"content_md": "a"},
            "submit_for_review": True,
        },
    )

    status = client.get(f"/api/v1/projects/{project['id']}/status", headers=auth_headers)
    assert status.status_code == 200
    body = status.json()
    assert body["experiment_counts_by_phase"]["review"] == 1
    assert len(body["recent_experiments"]) == 1
    assert len(body["active_experiments"]) == 1
    assert body["status_version"] == 1
