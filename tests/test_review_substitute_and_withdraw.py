"""Review substitute_kind, withdraw, and legacy_self_review (experiment plan v2 I3/I5)."""

from fastapi.testclient import TestClient


def _create_experiment_in_review(
    client: TestClient,
    headers: dict,
    project: dict,
    *,
    title: str = "substitute test",
) -> str:
    response = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=headers,
        json={"title": title, "plan": {"content_md": "## plan"}, "submit_for_review": True},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_admin_for_others_review_writes_audit_and_allows_approve(
    client: TestClient,
    auth_headers: dict,
    admin_headers: dict,
    project: dict,
):
    exp_id = _create_experiment_in_review(client, auth_headers, project)

    review = client.post(
        f"/api/v1/experiments/{exp_id}/reviews",
        headers=admin_headers,
        json={
            "reasonable_items": ["admin substitute OK"],
            "substitute_reason": "reviewer OOO",
        },
    )
    assert review.status_code == 201, review.text
    body = review.json()
    assert body["substitute_kind"] == "admin_for_others"

    audit = client.get(
        "/api/v1/audit",
        headers=admin_headers,
        params={"target_type": "experiment", "target_id": exp_id},
    )
    assert audit.status_code == 200
    actions = [row["action"] for row in audit.json()]
    assert "review_substitute" in actions

    approve = client.post(f"/api/v1/experiments/{exp_id}/approve", headers=auth_headers)
    assert approve.status_code == 200, approve.text


def test_admin_self_substitute_blocked_on_approve(
    client: TestClient,
    admin_headers: dict,
    project: dict,
):
    exp_id = _create_experiment_in_review(client, admin_headers, project)

    review = client.post(
        f"/api/v1/experiments/{exp_id}/reviews",
        headers=admin_headers,
        json={"reasonable_items": ["admin self substitute"]},
    )
    assert review.status_code == 201, review.text
    assert review.json()["substitute_kind"] == "admin_self_substitute"

    approve = client.post(f"/api/v1/experiments/{exp_id}/approve", headers=admin_headers)
    assert approve.status_code == 409
    assert approve.json()["reason"] == "creator_only_review"


def test_admin_for_others_requires_reason(
    client: TestClient,
    auth_headers: dict,
    admin_headers: dict,
    project: dict,
):
    exp_id = _create_experiment_in_review(client, auth_headers, project)

    review = client.post(
        f"/api/v1/experiments/{exp_id}/reviews",
        headers=admin_headers,
        json={"reasonable_items": ["missing reason"]},
    )
    assert review.status_code == 409


def test_reviewer_can_withdraw_clean_review(
    client: TestClient,
    auth_headers: dict,
    reviewer: dict,
    project: dict,
):
    exp_id = _create_experiment_in_review(client, auth_headers, project)

    created = client.post(
        f"/api/v1/experiments/{exp_id}/reviews",
        headers=reviewer["headers"],
        json={"reasonable_items": ["temporary"]},
    )
    assert created.status_code == 201, created.text
    review_id = created.json()["id"]

    withdrawn = client.post(
        f"/api/v1/experiments/{exp_id}/reviews/{review_id}/withdraw",
        headers=reviewer["headers"],
    )
    assert withdrawn.status_code == 204, withdrawn.text

    listing = client.get(f"/api/v1/experiments/{exp_id}/reviews", headers=auth_headers)
    assert listing.status_code == 200
    assert listing.json() == []


def test_reviewer_cannot_withdraw_after_item_activity(
    client: TestClient,
    auth_headers: dict,
    reviewer: dict,
    project: dict,
):
    exp_id = _create_experiment_in_review(client, auth_headers, project)

    created = client.post(
        f"/api/v1/experiments/{exp_id}/reviews",
        headers=reviewer["headers"],
        json={"unreasonable_items": ["needs work"]},
    )
    assert created.status_code == 201, created.text
    review_id = created.json()["id"]
    item_id = created.json()["items"][0]["id"]

    revised = client.post(
        f"/api/v1/experiments/{exp_id}/plans",
        headers=auth_headers,
        json={
            "content_md": "## revised plan",
            "addressed_item_ids": [item_id],
        },
    )
    assert revised.status_code == 201, revised.text

    withdrawn = client.post(
        f"/api/v1/experiments/{exp_id}/reviews/{review_id}/withdraw",
        headers=reviewer["headers"],
    )
    assert withdrawn.status_code == 409


def test_legacy_self_review_false_when_qualifying_review_exists(
    client: TestClient,
    auth_headers: dict,
    reviewer: dict,
    project: dict,
):
    exp_id = _create_experiment_in_review(client, auth_headers, project)
    review = client.post(
        f"/api/v1/experiments/{exp_id}/reviews",
        headers=reviewer["headers"],
        json={"reasonable_items": ["OK"]},
    )
    assert review.status_code == 201
    client.post(f"/api/v1/experiments/{exp_id}/approve", headers=auth_headers)

    detail = client.get(f"/api/v1/experiments/{exp_id}", headers=auth_headers)
    assert detail.status_code == 200
    assert detail.json()["legacy_self_review"] is False
