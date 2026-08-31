"""Tests for per-agent experiment actions and blocked_on (AC#3)."""

from fastapi.testclient import TestClient

from tests._frontmatter import make_valid_plan


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
        json={"title": title, "plan": {"content_md": make_valid_plan(body="## plan")}, "submit_for_review": True},
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
        json={"content_md": make_valid_plan(body="## plan v2"), "change_note": "address feedback"},
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


def test_prior_version_resolved_unblocks_creator_and_stays_pending_review(
    client, auth_headers, reviewer, project
):
    """Feedback 6eaa4700 + T8 (37bfd973) 修复：reviewer resolves every
    unreasonable item on prior plan version 时：

    (1) 单 review 函数 ``_prior_version_reviews_fully_resolved`` 仍返回 True
        → creator 可不依赖 v2 review 直接 approve（bd9b21f6 A7 carve-out，
        assert_approve_eligibility 单函数不变）
    (2) 批量 review 函数 ``prior_version_reviews_fully_resolved_by_experiment``
        在 v2 无 review 记录时返回 False（I1 修复：plan_version 上下文感知）
        → 实验保留在 reviewer 的 pending_reviews 队列，等 reviewer 重评 v2

    历史：该测试早期版本名 `clears_pending_review`，T8 修复后语义反向——
    v2 未评审时必须保留在队列，waker 才能唤醒 reviewer 重评，避免
    `revise → carve-out → invisible → never reviewed → never approve`
    路由死锁（T5-B e63ec33e 3h 滞留根因）。
    """
    valid_plan = (
        "---\n"
        "title: t\n"
        "acceptance:\n"
        "  - a\n"
        "evidence_keys:\n"
        "  - e\n"
        "dependencies:\n"
        "  - d\n"
        "---\n"
        "## body\n"
    )
    exp = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={
            "title": "prior-resolved",
            "plan": {"content_md": valid_plan},
            "submit_for_review": True,
        },
    )
    assert exp.status_code == 201, exp.text
    exp_id = exp.json()["id"]

    review = client.post(
        f"/api/v1/experiments/{exp_id}/reviews",
        headers=reviewer["headers"],
        json={"unreasonable_items": ["missing detail"]},
    )
    assert review.status_code == 201, review.text
    item_id = review.json()["items"][0]["id"]

    # open -> rebutted (creator) -> resolved/closed (reviewer): the legal
    # path to a fully-resolved unreasonable item (open -> resolved is rejected
    # by the state machine).
    rebuted = client.patch(
        f"/api/v1/review-items/{item_id}",
        headers=auth_headers,
        json={"status": "rebutted"},
    )
    assert rebuted.status_code == 200, rebuted.text
    resolved = client.patch(
        f"/api/v1/review-items/{item_id}",
        headers=reviewer["headers"],
        json={"status": "resolved"},
    )
    assert resolved.status_code == 200, resolved.text

    revise = client.post(
        f"/api/v1/experiments/{exp_id}/plans",
        headers=auth_headers,
        json={
            "content_md": valid_plan.replace("title: t", "title: t-v2"),
            "change_note": "address feedback",
        },
    )
    assert revise.status_code == 201, revise.text

    # Creator capabilities: prior unreasonable fully resolved → may approve
    # without a fresh current-version review (bd9b21f6 A7 carve-out 仍生效)。
    detail = client.get(f"/api/v1/experiments/{exp_id}", headers=auth_headers).json()
    assert detail["current_plan_version"] == 2
    assert "approve" in detail["actions"]

    # T8 修复后：v2 无 review 记录 → 实验必须留在 pending_reviews 队列
    # （路由层强制 reviewer 重评 v2，避免 carve-out 误排除）。
    reviewer_todos = client.get(
        "/api/v1/agents/me/todos", headers=reviewer["headers"]
    ).json()
    assert any(e["id"] == exp_id for e in reviewer_todos["pending_reviews"]), (
        "T8 I1 修复目标：v1 resolved + revise v2 后实验必须出现在 pending_reviews 队列"
    )


def test_same_content_plan_revise_noops_after_clean_review(
    client, auth_headers, reviewer, project
):
    exp_id = _create_experiment_in_review(client, auth_headers, project)

    review = client.post(
        f"/api/v1/experiments/{exp_id}/reviews",
        headers=reviewer["headers"],
        json={"reasonable_items": ["v1 looks good"]},
    )
    assert review.status_code == 201, review.text

    revise = client.post(
        f"/api/v1/experiments/{exp_id}/plans",
        headers=auth_headers,
        json={"content_md": make_valid_plan(body="## plan"), "change_note": "note only"},
    )
    assert revise.status_code == 201, revise.text
    assert revise.json()["version"] == 1

    detail = client.get(f"/api/v1/experiments/{exp_id}", headers=auth_headers).json()
    assert detail["current_plan_version"] == 1
    assert detail["blocked_on"] == "none"
    assert detail["actions"] == ["approve", "withdraw"]


def test_creator_first_review_still_awaiting_non_creator(
    client, auth_headers, project
):
    exp_id = _create_experiment_in_review(client, auth_headers, project)

    detail = client.get(f"/api/v1/experiments/{exp_id}", headers=auth_headers).json()
    assert detail["blocked_on"] == "awaiting_non_creator_review"
    assert detail["actions"] == []
