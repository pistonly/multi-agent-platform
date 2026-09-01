"""T41: list endpoints pagination consistency + category normalization helper.

Pins the acceptance:

1. ``GET /agents`` / ``GET /webhooks`` / ``GET /projects`` gain
   ``page`` / ``page_size`` + ``X-Total-Count`` (same style as topics /
   audit lists). Default page_size=100 keeps existing small-scale callers
   returning full lists.
2. The duplicated notification-category normalization blocks in
   ``/me/work`` and ``/me/notifications`` collapse into
   ``_normalize_notification_category``; the 422 payload keeps the
   parameter name in the detail message.
"""

from __future__ import annotations


def test_list_agents_pagination_and_total_header(client, admin_headers, project):
    for i in range(3):
        client.post(
            "/api/v1/agents",
            headers=admin_headers,
            json={
                "name": f"t41-agent-{i}",
                "role": "agent",
                "project_key": project["project_key"],
            },
        )

    page1 = client.get(
        "/api/v1/agents", headers=admin_headers, params={"page": 1, "page_size": 2}
    )
    assert page1.status_code == 200
    assert len(page1.json()) == 2
    total = int(page1.headers["X-Total-Count"])
    assert total >= 4  # admin + 3 created agents

    page2 = client.get(
        "/api/v1/agents", headers=admin_headers, params={"page": 2, "page_size": 2}
    )
    assert page2.status_code == 200
    ids1 = {item["id"] for item in page1.json()}
    ids2 = {item["id"] for item in page2.json()}
    assert not (ids1 & ids2)
    assert len(ids1 | ids2) == min(total, 4)


def test_list_webhooks_pagination_and_total_header(client, admin_headers):
    for i in range(2):
        created = client.post(
            "/api/v1/webhooks",
            headers=admin_headers,
            json={"url": f"http://example.com/t41-{i}", "events": []},
        )
        assert created.status_code == 201, created.text

    page1 = client.get(
        "/api/v1/webhooks", headers=admin_headers, params={"page": 1, "page_size": 1}
    )
    assert page1.status_code == 200
    assert len(page1.json()) == 1
    assert page1.headers["X-Total-Count"] == "2"

    page2 = client.get(
        "/api/v1/webhooks", headers=admin_headers, params={"page": 2, "page_size": 1}
    )
    assert page1.json()[0]["id"] != page2.json()[0]["id"]


def test_list_projects_pagination_and_total_header(client, admin_headers):
    for i in range(3):
        created = client.post(
            "/api/v1/projects",
            headers=admin_headers,
            json={
                "project_key": f"t41-proj-{i}",
                "name": f"T41 Project {i}",
                "workspace_path": f"/tmp/t41-{i}",
            },
        )
        assert created.status_code == 201, created.text

    page1 = client.get(
        "/api/v1/projects", headers=admin_headers, params={"page": 1, "page_size": 2}
    )
    assert page1.status_code == 200
    assert len(page1.json()) == 2
    total = int(page1.headers["X-Total-Count"])
    assert total == 3  # the three created above (no project fixture in this test)

    page2 = client.get(
        "/api/v1/projects", headers=admin_headers, params={"page": 2, "page_size": 2}
    )
    keys1 = {item["id"] for item in page1.json()}
    keys2 = {item["id"] for item in page2.json()}
    assert not (keys1 & keys2)


def test_notification_category_422_mentions_param_name(client, auth_headers):
    resp = client.get(
        "/api/v1/agents/me/notifications", headers=auth_headers, params={"category": "bogus"}
    )
    assert resp.status_code == 422
    assert "category must be wakeable, digest, or all" in resp.text

    resp = client.get(
        "/api/v1/agents/me/work",
        headers=auth_headers,
        params={"notification_category": "bogus"},
    )
    assert resp.status_code == 422
    assert "notification_category must be wakeable, digest, or all" in resp.text
