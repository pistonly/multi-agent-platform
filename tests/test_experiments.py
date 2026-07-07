import pytest

pytestmark = pytest.mark.slow


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


def test_experiment_status_projects_acceptance_status(client, auth_headers, project):
    create = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={
            "title": "Acceptance status",
            "plan": {
                "content_md": "\n".join(
                    [
                        "## 验收标准",
                        "- [acceptance_type: unit_test] pytest 覆盖解析",
                        "- [acceptance_type: manual] 人工确认 CLI 输出",
                    ]
                )
            },
        },
    )
    assert create.status_code == 201
    experiment_id = create.json()["id"]

    detail = client.get(f"/api/v1/experiments/{experiment_id}", headers=auth_headers)

    assert detail.status_code == 200
    statuses = detail.json()["acceptance_status"]
    assert [item["acceptance_type"] for item in statuses] == ["unit_test", "manual"]
    assert statuses[0]["description"] == "pytest 覆盖解析"
    assert statuses[0]["id"].startswith("acc-")
    assert statuses[0]["evidence_provided"] is False
    assert statuses[0]["reviewer_verdict"] is None


def test_experiment_status_projects_completion_evidence(client, auth_headers, reviewer, project):
    create = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={
            "title": "Acceptance evidence",
            "plan": {
                "content_md": "- [acceptance_type: smoke] API health smoke passes"
            },
            "submit_for_review": True,
        },
    )
    assert create.status_code == 201
    experiment_id = create.json()["id"]

    review = client.post(
        f"/api/v1/experiments/{experiment_id}/reviews",
        headers=reviewer["headers"],
        json={"reasonable_items": ["OK"]},
    )
    assert review.status_code == 201
    assert client.post(
        f"/api/v1/experiments/{experiment_id}/approve",
        headers=auth_headers,
    ).status_code == 200
    assert client.post(
        f"/api/v1/experiments/{experiment_id}/start",
        headers=auth_headers,
    ).status_code == 200

    complete = client.post(
        f"/api/v1/experiments/{experiment_id}/complete",
        headers=auth_headers,
        json={
            "summary": "提交结果",
            "content_md": "结果满足计划验收标准",
            "metadata": {"pytest_summary": "1 passed"},
        },
    )
    assert complete.status_code == 200

    detail = client.get(f"/api/v1/experiments/{experiment_id}", headers=auth_headers)

    assert detail.status_code == 200
    statuses = detail.json()["acceptance_status"]
    assert len(statuses) == 1
    assert statuses[0]["evidence_provided"] is True


def test_experiment_status_rejects_unknown_acceptance_type(client, auth_headers, project):
    create = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={
            "title": "Bad acceptance",
            "plan": {
                "content_md": "- [acceptance_type: mystery] 不允许静默降级"
            },
        },
    )
    assert create.status_code == 201
    experiment_id = create.json()["id"]

    detail = client.get(f"/api/v1/experiments/{experiment_id}", headers=auth_headers)

    assert detail.status_code == 422
    assert "Unknown acceptance_type 'mystery'" in detail.json()["detail"]
    assert "unit_test" in detail.json()["detail"]


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
