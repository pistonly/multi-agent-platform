"""Review eligibility: creator self-review block and approve gates."""

from fastapi.testclient import TestClient


def _create_experiment_in_review(
    client: TestClient,
    headers: dict,
    project: dict,
    *,
    title: str = "eligibility test",
) -> str:
    response = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=headers,
        json={"title": title, "plan": {"content_md": "## plan"}, "submit_for_review": True},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_creator_cannot_create_review(client, auth_headers, project, agent_token):
    exp_id = _create_experiment_in_review(client, auth_headers, project)
    agent_id, _ = agent_token

    response = client.post(
        f"/api/v1/experiments/{exp_id}/reviews",
        headers=auth_headers,
        json={"reasonable_items": ["looks fine"]},
    )
    assert response.status_code == 403
    body = response.json()
    assert body["reason"] == "creator_self_review_blocked"
    assert body["actor_id"] == agent_id
    assert body["experiment_id"] == exp_id


def test_reviewer_can_create_review(client, auth_headers, reviewer, project):
    exp_id = _create_experiment_in_review(client, auth_headers, project)

    response = client.post(
        f"/api/v1/experiments/{exp_id}/reviews",
        headers=reviewer["headers"],
        json={"reasonable_items": ["OK"]},
    )
    assert response.status_code == 201, response.text


def test_approve_without_review_returns_no_review(client, auth_headers, project):
    exp_id = _create_experiment_in_review(client, auth_headers, project)

    response = client.post(f"/api/v1/experiments/{exp_id}/approve", headers=auth_headers)
    assert response.status_code == 409
    assert response.json()["reason"] == "no_review"


def test_approve_with_only_creator_review_blocked(client, admin_headers, project):
    exp_id = _create_experiment_in_review(client, admin_headers, project)

    review = client.post(
        f"/api/v1/experiments/{exp_id}/reviews",
        headers=admin_headers,
        json={"reasonable_items": ["admin self-review"]},
    )
    assert review.status_code == 201, review.text

    response = client.post(f"/api/v1/experiments/{exp_id}/approve", headers=admin_headers)
    assert response.status_code == 409
    assert response.json()["reason"] == "creator_only_review"


def test_approve_with_open_unreasonable_returns_conflict(client, auth_headers, reviewer, project):
    exp_id = _create_experiment_in_review(client, auth_headers, project)

    review = client.post(
        f"/api/v1/experiments/{exp_id}/reviews",
        headers=reviewer["headers"],
        json={"unreasonable_items": ["needs more detail"]},
    )
    assert review.status_code == 201, review.text

    response = client.post(f"/api/v1/experiments/{exp_id}/approve", headers=auth_headers)
    assert response.status_code == 409
    assert response.json()["reason"] == "open_unreasonable_item"


def test_approve_after_plan_revise_without_current_review(
    client, auth_headers, reviewer, project
):
    exp_id = _create_experiment_in_review(client, auth_headers, project)

    review = client.post(
        f"/api/v1/experiments/{exp_id}/reviews",
        headers=reviewer["headers"],
        json={"reasonable_items": ["v1 ok"]},
    )
    assert review.status_code == 201, review.text

    revise = client.post(
        f"/api/v1/experiments/{exp_id}/plans",
        headers=auth_headers,
        json={"content_md": "## plan v2", "change_note": "revise"},
    )
    assert revise.status_code == 201, revise.text

    response = client.post(f"/api/v1/experiments/{exp_id}/approve", headers=auth_headers)
    assert response.status_code == 409
    assert response.json()["reason"] == "no_review_for_current_plan_version"
