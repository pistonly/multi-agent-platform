import server.services.webhook_service as webhook_service


def test_webhook_admin_only(client, auth_headers):
    resp = client.post(
        "/api/v1/webhooks", headers=auth_headers, json={"url": "http://x/hook", "events": []}
    )
    assert resp.status_code == 403


def test_webhook_crud_and_delivery(client, admin_headers, project, monkeypatch):
    calls = []

    def fake_post(url, body, signature):
        calls.append((url, body, signature))
        return 200

    monkeypatch.setattr(webhook_service, "_http_post", fake_post)

    created = client.post(
        "/api/v1/webhooks",
        headers=admin_headers,
        json={"url": "http://example.com/hook", "events": ["experiment.created"]},
    )
    assert created.status_code == 201
    webhook = created.json()
    assert webhook["secret"]
    assert webhook["url"] == "http://example.com/hook"
    assert "secret" not in client.get("/api/v1/webhooks", headers=admin_headers).json()[0]

    # 触发事件：创建实验
    client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=admin_headers,
        json={"title": "触发 webhook", "plan": {"content_md": "p"}},
    )
    assert len(calls) == 1
    assert b"experiment.created" in calls[0][1]
    assert calls[0][2].startswith("sha256=")

    deliveries = client.get(
        f"/api/v1/webhooks/{webhook['id']}/deliveries", headers=admin_headers
    ).json()
    assert len(deliveries) == 1
    assert deliveries[0]["success"] is True
    assert deliveries[0]["status_code"] == 200

    # 不订阅的事件不投递（创建话题 → topic.created）
    client.post(
        f"/api/v1/projects/{project['id']}/topics",
        headers=admin_headers,
        json={"title": "不应触发"},
    )
    assert len(calls) == 1

    updated = client.patch(
        f"/api/v1/webhooks/{webhook['id']}", headers=admin_headers, json={"active": False}
    )
    assert updated.json()["active"] is False

    deleted = client.delete(f"/api/v1/webhooks/{webhook['id']}", headers=admin_headers)
    assert deleted.status_code == 204


def test_audit_trail(client, auth_headers, admin_headers, project):
    exp = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={"title": "审计追踪", "plan": {"content_md": "p"}},
    ).json()

    target_audit = client.get(
        "/api/v1/audit",
        params={"target_type": "experiment", "target_id": exp["id"]},
        headers=auth_headers,
    )
    assert target_audit.status_code == 200
    actions = [a["action"] for a in target_audit.json()]
    assert "experiment.created" in actions

    global_audit = client.get("/api/v1/admin/audit", headers=admin_headers)
    assert global_audit.status_code == 200
    assert int(global_audit.headers["X-Total-Count"]) >= 1

    forbidden = client.get("/api/v1/admin/audit", headers=auth_headers)
    assert forbidden.status_code == 403


def test_audit_cross_project_forbidden(client, auth_headers, admin_headers, project):
    other = client.post(
        "/api/v1/projects",
        headers=admin_headers,
        json={"project_key": "audit-other", "name": "Other", "workspace_path": "/tmp/other"},
    ).json()
    exp = client.post(
        f"/api/v1/projects/{other['id']}/experiments",
        headers=admin_headers,
        json={"title": "别的项目实验", "plan": {"content_md": "p"}},
    ).json()

    resp = client.get(
        "/api/v1/audit",
        params={"target_type": "experiment", "target_id": exp["id"]},
        headers=auth_headers,
    )
    assert resp.status_code == 403


def test_project_webhook_receives_review_submitted(client, admin_headers, project, monkeypatch):
    calls = []

    def fake_post(url, body, signature):
        calls.append(body)
        return 200

    monkeypatch.setattr(webhook_service, "_http_post", fake_post)

    client.post(
        "/api/v1/webhooks",
        headers=admin_headers,
        json={
            "project_id": project["id"],
            "url": "http://example.com/hook",
            "events": ["review.submitted"],
        },
    )
    exp = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=admin_headers,
        json={"title": "待评审", "plan": {"content_md": "p"}, "submit_for_review": True},
    ).json()
    client.post(
        f"/api/v1/experiments/{exp['id']}/reviews",
        headers=admin_headers,
        json={"reasonable_items": ["合理"], "unreasonable_items": []},
    )

    assert len(calls) == 1
    assert b"review.submitted" in calls[0]


def test_webhook_retries_on_5xx(client, admin_headers, project, monkeypatch):
    """5xx 应触发重试，最终成功后 attempts=2、success=True、last_error=None。"""
    call_statuses = [503, 200]

    def fake_post(url, body, signature):
        return call_statuses.pop(0)

    monkeypatch.setattr(webhook_service, "_http_post", fake_post)
    monkeypatch.setattr(webhook_service, "time", type("M", (), {"sleep": lambda *a, **kw: None})())

    client.post(
        "/api/v1/webhooks",
        headers=admin_headers,
        json={"url": "http://example.com/hook", "events": ["experiment.created"]},
    )
    client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=admin_headers,
        json={"title": "重试一次", "plan": {"content_md": "p"}},
    )

    deliveries = client.get(
        "/api/v1/webhooks", headers=admin_headers
    ).json()
    wh_id = deliveries[0]["id"]
    deliveries = client.get(
        f"/api/v1/webhooks/{wh_id}/deliveries", headers=admin_headers
    ).json()
    assert deliveries[0]["success"] is True
    assert deliveries[0]["attempts"] == 2
    assert deliveries[0]["status_code"] == 200
    assert deliveries[0]["last_error"] is None


def test_webhook_no_retry_on_4xx(client, admin_headers, project, monkeypatch):
    """4xx (非 408/429) 不重试，attempts=1、success=False、last_error='HTTP 400'。"""
    def fake_post(url, body, signature):
        return 400

    monkeypatch.setattr(webhook_service, "_http_post", fake_post)
    monkeypatch.setattr(webhook_service, "time", type("M", (), {"sleep": lambda *a, **kw: None})())

    client.post(
        "/api/v1/webhooks",
        headers=admin_headers,
        json={"url": "http://example.com/hook", "events": ["experiment.created"]},
    )
    client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=admin_headers,
        json={"title": "不重试", "plan": {"content_md": "p"}},
    )

    wh_id = client.get("/api/v1/webhooks", headers=admin_headers).json()[0]["id"]
    delivery = client.get(
        f"/api/v1/webhooks/{wh_id}/deliveries", headers=admin_headers
    ).json()[0]
    assert delivery["success"] is False
    assert delivery["attempts"] == 1
    assert delivery["status_code"] == 400
    assert delivery["last_error"] == "HTTP 400"


def test_webhook_records_exception(client, admin_headers, project, monkeypatch):
    """网络异常应重试至 max_attempts，最后 last_error 包含异常类型名。"""
    class _ConnError(Exception):
        pass

    def fake_post(url, body, signature):
        raise _ConnError("connection refused")

    monkeypatch.setattr(webhook_service, "_http_post", fake_post)
    monkeypatch.setattr(webhook_service, "time", type("M", (), {"sleep": lambda *a, **kw: None})())

    client.post(
        "/api/v1/webhooks",
        headers=admin_headers,
        json={"url": "http://example.com/hook", "events": ["experiment.created"]},
    )
    client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=admin_headers,
        json={"title": "网络异常", "plan": {"content_md": "p"}},
    )

    wh_id = client.get("/api/v1/webhooks", headers=admin_headers).json()[0]["id"]
    delivery = client.get(
        f"/api/v1/webhooks/{wh_id}/deliveries", headers=admin_headers
    ).json()[0]
    assert delivery["success"] is False
    assert delivery["attempts"] == webhook_service.DEFAULT_MAX_ATTEMPTS
    assert delivery["status_code"] is None
    assert "_ConnError" in delivery["last_error"]
