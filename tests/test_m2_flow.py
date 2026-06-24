import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def reviewer(client: TestClient) -> dict[str, str]:
    response = client.post("/api/v1/agents", params={"name": "reviewer-agent"})
    assert response.status_code == 201
    data = response.json()
    return {
        "id": data["id"],
        "headers": {"Authorization": f"Bearer {data['api_token']}"},
    }


@pytest.fixture
def experiment_in_review(client: TestClient, auth_headers: dict[str, str]) -> dict:
    project = client.post(
        "/api/v1/projects",
        headers=auth_headers,
        json={"name": "M2 项目", "workspace_path": "/tmp/m2"},
    ).json()
    experiment = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={
            "title": "争议实验",
            "plan": {"content_md": "## v1\n初始计划"},
            "submit_for_review": True,
        },
    ).json()
    return {"project_id": project["id"], **experiment}


def test_full_review_flow(client, auth_headers, reviewer, experiment_in_review):
    exp_id = experiment_in_review["id"]

    review = client.post(
        f"/api/v1/experiments/{exp_id}/reviews",
        headers=reviewer["headers"],
        json={
            "reasonable_items": ["目标清晰"],
            "unreasonable_items": ["缺少温度控制", "缺少回滚方案"],
        },
    )
    assert review.status_code == 201
    unreasonable = [i for i in review.json()["items"] if i["kind"] == "unreasonable"]
    assert len(unreasonable) == 2
    assert all(i["status"] == "open" for i in unreasonable)

    detail = client.get(f"/api/v1/experiments/{exp_id}", headers=auth_headers)
    assert detail.json()["open_unreasonable_count"] == 2

    cannot_approve = client.post(f"/api/v1/experiments/{exp_id}/approve", headers=auth_headers)
    assert cannot_approve.status_code == 422

    item_ids = [i["id"] for i in unreasonable]
    revised = client.post(
        f"/api/v1/experiments/{exp_id}/plans",
        headers=auth_headers,
        json={
            "content_md": "## v2\n补充温度与回滚",
            "change_note": "address review",
            "addressed_item_ids": item_ids,
        },
    )
    assert revised.status_code == 201
    assert revised.json()["version"] == 2

    reviews = client.get(f"/api/v1/experiments/{exp_id}/reviews", headers=auth_headers).json()
    addressed = [i for r in reviews for i in r["items"] if i["kind"] == "unreasonable"]
    assert all(i["status"] == "addressed" for i in addressed)

    for item in addressed:
        resolved = client.patch(
            f"/api/v1/review-items/{item['id']}",
            headers=reviewer["headers"],
            json={"status": "resolved"},
        )
        assert resolved.status_code == 200
        assert resolved.json()["status"] == "resolved"

    approved = client.post(f"/api/v1/experiments/{exp_id}/approve", headers=auth_headers)
    assert approved.status_code == 200
    assert approved.json()["phase"] == "approved"

    detail_after = client.get(f"/api/v1/experiments/{exp_id}", headers=auth_headers).json()
    assert detail_after["open_unreasonable_count"] == 0
    assert detail_after["current_plan_version"] == 2


def test_rebuttal_flow(client, auth_headers, reviewer, experiment_in_review):
    exp_id = experiment_in_review["id"]
    review = client.post(
        f"/api/v1/experiments/{exp_id}/reviews",
        headers=reviewer["headers"],
        json={"unreasonable_items": ["样本量不足"]},
    ).json()
    item_id = next(i["id"] for i in review["items"] if i["kind"] == "unreasonable")

    rebutted = client.patch(
        f"/api/v1/review-items/{item_id}",
        headers=auth_headers,
        json={"status": "rebutted"},
    )
    assert rebutted.status_code == 200

    resolved = client.patch(
        f"/api/v1/review-items/{item_id}",
        headers=reviewer["headers"],
        json={"status": "resolved"},
    )
    assert resolved.status_code == 200

    approved = client.post(f"/api/v1/experiments/{exp_id}/approve", headers=auth_headers)
    assert approved.status_code == 200


def test_submit_withdraw_cancel(client, auth_headers, experiment_in_review):
    exp_id = experiment_in_review["id"]

    withdrawn = client.post(f"/api/v1/experiments/{exp_id}/withdraw", headers=auth_headers)
    assert withdrawn.status_code == 200
    assert withdrawn.json()["phase"] == "draft"

    submitted = client.post(f"/api/v1/experiments/{exp_id}/submit-review", headers=auth_headers)
    assert submitted.status_code == 200
    assert submitted.json()["phase"] == "review"

    cancelled = client.post(f"/api/v1/experiments/{exp_id}/cancel", headers=auth_headers)
    assert cancelled.status_code == 200
    assert cancelled.json()["phase"] == "cancelled"


def test_comments_tree(client, auth_headers, experiment_in_review):
    exp_id = experiment_in_review["id"]
    plan_id = client.get(f"/api/v1/experiments/{exp_id}", headers=auth_headers).json()["current_plan"]["id"]

    root = client.post(
        f"/api/v1/experiments/{exp_id}/comments",
        headers=auth_headers,
        json={
            "anchor_type": "plan",
            "anchor_id": plan_id,
            "body": "请补充细节",
        },
    )
    assert root.status_code == 201
    root_id = root.json()["id"]

    reply = client.post(
        f"/api/v1/experiments/{exp_id}/comments",
        headers=auth_headers,
        json={
            "anchor_type": "comment",
            "anchor_id": root_id,
            "parent_id": root_id,
            "body": "已在 v2 补充",
        },
    )
    assert reply.status_code == 201

    tree = client.get(f"/api/v1/experiments/{exp_id}/comments?tree=true", headers=auth_headers)
    assert tree.status_code == 200
    assert len(tree.json()) == 1
    assert len(tree.json()[0]["children"]) == 1


def test_duplicate_review_conflict(client, auth_headers, reviewer, experiment_in_review):
    exp_id = experiment_in_review["id"]
    payload = {"reasonable_items": ["ok"]}
    assert client.post(f"/api/v1/experiments/{exp_id}/reviews", headers=reviewer["headers"], json=payload).status_code == 201
    assert client.post(f"/api/v1/experiments/{exp_id}/reviews", headers=reviewer["headers"], json=payload).status_code == 409


def test_plan_history(client, auth_headers, experiment_in_review):
    exp_id = experiment_in_review["id"]
    plans = client.get(f"/api/v1/experiments/{exp_id}/plans", headers=auth_headers)
    assert plans.status_code == 200
    assert len(plans.json()) == 1

    plan_v1 = client.get(f"/api/v1/experiments/{exp_id}/plans/1", headers=auth_headers)
    assert plan_v1.status_code == 200
    assert plan_v1.json()["version"] == 1
