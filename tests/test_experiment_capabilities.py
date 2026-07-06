"""Tests for per-agent experiment actions and blocked_on (AC#3)."""

from fastapi.testclient import TestClient


def _create_experiment_in_review(
    client: TestClient,
    headers: dict,
    project: dict,
    *,
    title: str = "capabilities test",
) -> str:
    response = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=headers,
        json={"title": title, "plan": {"content_md": "## plan"}, "submit_for_review": True},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_creator_review_open_unreasonable_capabilities(
    client, auth_headers, reviewer, project
):
    exp_id = _create_experiment_in_review(client, auth_headers, project)

    client.post(
        f"/api/v1/experiments/{exp_id}/reviews",
        headers=reviewer["headers"],
        json={"unreasonable_items": ["needs more detail"]},
    )

    detail = client.get(f"/api/v1/experiments/{exp_id}", headers=auth_headers).json()
    assert detail["blocked_on"] == "open_unreasonable_item"
    assert "plan_revise" in detail["actions"]
    assert "approve" not in detail["actions"]

    todos = client.get("/api/v1/agents/me/todos", headers=auth_headers).json()
    creator_exp = next(e for e in todos["my_open_experiments"] if e["id"] == exp_id)
    assert creator_exp["blocked_on"] == "open_unreasonable_item"
    assert "plan_revise" in creator_exp["actions"]


def test_creator_review_clean_capabilities(client, auth_headers, reviewer, project):
    exp_id = _create_experiment_in_review(client, auth_headers, project)

    client.post(
        f"/api/v1/experiments/{exp_id}/reviews",
        headers=reviewer["headers"],
        json={"reasonable_items": ["looks good"]},
    )

    detail = client.get(f"/api/v1/experiments/{exp_id}", headers=auth_headers).json()
    assert detail["blocked_on"] == "none"
    assert detail["actions"] == ["approve", "withdraw"]


def test_reviewer_pending_reviews_capabilities(client, auth_headers, reviewer, project):
    exp_id = _create_experiment_in_review(client, auth_headers, project)

    todos = client.get("/api/v1/agents/me/todos", headers=reviewer["headers"]).json()
    pending = next(e for e in todos["pending_reviews"] if e["id"] == exp_id)
    assert pending["blocked_on"] == "none"
    assert pending["actions"] == ["review_add"]

    detail = client.get(f"/api/v1/experiments/{exp_id}", headers=reviewer["headers"]).json()
    assert detail["blocked_on"] == "none"
    assert detail["actions"] == ["review_add"]


def test_creator_blocked_after_plan_revise_without_rereview(
    client, auth_headers, reviewer, project
):
    exp_id = _create_experiment_in_review(client, auth_headers, project)

    review = client.post(
        f"/api/v1/experiments/{exp_id}/reviews",
        headers=reviewer["headers"],
        json={"reasonable_items": ["v1 looks good"]},
    )
    assert review.status_code == 201, review.text

    detail = client.get(f"/api/v1/experiments/{exp_id}", headers=auth_headers).json()
    assert detail["blocked_on"] == "none"
    assert detail["actions"] == ["approve", "withdraw"]

    revise = client.post(
        f"/api/v1/experiments/{exp_id}/plans",
        headers=auth_headers,
        json={"content_md": "## plan v2", "change_note": "address feedback"},
    )
    assert revise.status_code == 201, revise.text

    detail = client.get(f"/api/v1/experiments/{exp_id}", headers=auth_headers).json()
    assert detail["current_plan_version"] == 2
    assert detail["blocked_on"] == "awaiting_review_for_current_plan_version"
    assert detail["actions"] == []

    todos = client.get("/api/v1/agents/me/todos", headers=auth_headers).json()
    creator_exp = next(e for e in todos["my_open_experiments"] if e["id"] == exp_id)
    assert creator_exp["blocked_on"] == "awaiting_review_for_current_plan_version"

    reviewer_todos = client.get("/api/v1/agents/me/todos", headers=reviewer["headers"]).json()
    pending = next(e for e in reviewer_todos["pending_reviews"] if e["id"] == exp_id)
    assert pending["actions"] == ["review_add"]


def test_creator_first_review_still_awaiting_non_creator(
    client, auth_headers, project
):
    exp_id = _create_experiment_in_review(client, auth_headers, project)

    detail = client.get(f"/api/v1/experiments/{exp_id}", headers=auth_headers).json()
    assert detail["blocked_on"] == "awaiting_non_creator_review"
    assert detail["actions"] == []
