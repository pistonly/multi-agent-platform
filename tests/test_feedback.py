import pytest
from map_types import (
    FeedbackCategory,
    FeedbackStatus,
    PlatformFeedbackCreate,
    PlatformFeedbackUpdate,
)

pytestmark = pytest.mark.slow


def _submit(client, headers, **overrides):
    payload = {"body": "反馈：希望支持 Markdown 导出"}
    payload.update(overrides)
    resp = client.post("/api/v1/feedback", headers=headers, json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_any_agent_can_submit_feedback(client, auth_headers):
    fb = _submit(client, auth_headers)
    assert fb["status"] == "new"
    assert fb["category"] is None
    assert fb["body"].startswith("反馈")
    assert fb["author_name"] == "test-agent"
    # project-bound agent defaults to its own project as source context
    assert fb["project_id"] is not None


def test_admin_submit_global_feedback(client, admin_headers):
    fb = _submit(client, admin_headers, body="admin 全局反馈")
    # admin has no project -> feedback stays global (project_id None)
    assert fb["project_id"] is None


def test_non_admin_cannot_list_feedback(client, auth_headers):
    resp = client.get("/api/v1/feedback", headers=auth_headers)
    assert resp.status_code == 403


def test_admin_list_filter_and_total(client, admin_headers, auth_headers):
    _submit(client, auth_headers, body="bug 反馈", category="bug")
    _submit(client, auth_headers, body="一条建议", category="suggestion")

    all_fb = client.get("/api/v1/feedback", headers=admin_headers)
    assert all_fb.status_code == 200
    assert all_fb.headers["X-Total-Count"] == "2"
    assert len(all_fb.json()) == 2

    bugs = client.get("/api/v1/feedback?category=bug", headers=admin_headers)
    assert bugs.headers["X-Total-Count"] == "1"
    assert len(bugs.json()) == 1

    new_only = client.get("/api/v1/feedback?status=new", headers=admin_headers)
    assert new_only.headers["X-Total-Count"] == "2"


def test_admin_triage_update(client, admin_headers, auth_headers):
    fb = _submit(client, auth_headers, body="待分类")
    updated = client.patch(
        f"/api/v1/feedback/{fb['id']}",
        headers=admin_headers,
        json={"status": "in_progress", "category": "question"},
    )
    assert updated.status_code == 200
    assert updated.json()["status"] == "in_progress"
    assert updated.json()["category"] == "question"


def test_non_admin_cannot_triage(client, auth_headers):
    fb = _submit(client, auth_headers, body="x")
    denied = client.patch(
        f"/api/v1/feedback/{fb['id']}", headers=auth_headers, json={"status": "resolved"}
    )
    assert denied.status_code == 403


def test_admin_can_archive_feedback(client, admin_headers, auth_headers):
    fb = _submit(client, auth_headers, body="已处理")
    client.patch(f"/api/v1/feedback/{fb['id']}", headers=admin_headers, json={"archived": True})
    active = client.get("/api/v1/feedback", headers=admin_headers)
    assert active.headers["X-Total-Count"] == "0"
    with_archived = client.get(
        "/api/v1/feedback?include_archived=true", headers=admin_headers
    )
    assert with_archived.headers["X-Total-Count"] == "1"


def test_feedback_pagination(client, admin_headers, auth_headers):
    for i in range(5):
        _submit(client, auth_headers, body=f"反馈 {i}")
    page1 = client.get("/api/v1/feedback?page=1&page_size=2", headers=admin_headers)
    assert page1.headers["X-Total-Count"] == "5"
    assert len(page1.json()) == 2
    page2 = client.get("/api/v1/feedback?page=2&page_size=2", headers=admin_headers)
    assert len(page2.json()) == 2
    page3 = client.get("/api/v1/feedback?page=3&page_size=2", headers=admin_headers)
    assert len(page3.json()) == 1


def test_empty_body_rejected(client, auth_headers):
    resp = client.post("/api/v1/feedback", headers=auth_headers, json={"body": ""})
    assert resp.status_code == 422


def test_sdk_submit_and_admin_triage(map_client, admin_map_client):
    fb = map_client.submit_feedback(PlatformFeedbackCreate(body="SDK 反馈"))
    assert fb.status == FeedbackStatus.new
    assert fb.body == "SDK 反馈"

    items, total = admin_map_client.list_feedback_page()
    assert total >= 1
    assert any(item.body == "SDK 反馈" for item in items)

    updated = admin_map_client.update_feedback(
        fb.id,
        PlatformFeedbackUpdate(status=FeedbackStatus.resolved, category=FeedbackCategory.bug),
    )
    assert updated.status == FeedbackStatus.resolved
    assert updated.category == FeedbackCategory.bug
