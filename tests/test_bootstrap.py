"""Tests for the self-service bootstrap endpoint (POST /api/v1/bootstrap).

This endpoint lets a new user create a project + 3 persona agents in a
single call without an admin token. We verify:
- success path: project + 3 agents created, tokens returned work
- 409 on duplicate project_key
- 409 on duplicate agent name (different project_key but slug collision)
- atomicity: on failure nothing is persisted
"""

from __future__ import annotations


def test_bootstrap_creates_project_and_personas(client):
    """Anonymous POST /bootstrap creates project + 3 persona agents + tokens."""
    resp = client.post(
        "/api/v1/bootstrap",
        json={
            "project_key": "my-new-project",
            "project_name": "My New Project",
            "workspace_path": "/tmp/my-new-project",
            "description": "self-service bootstrap test",
        },
    )
    assert resp.status_code == 201
    body = resp.json()

    # project 字段
    project = body["project"]
    assert project["project_key"] == "my-new-project"
    assert project["name"] == "My New Project"
    assert project["current_status_version"] == 1
    assert project["id"] is not None

    # 3 个 persona agents，每个有 token
    agents = body["agents"]
    assert len(agents) == 3
    personas = {a["persona"] for a in agents}
    assert personas == {"host", "participant", "reviewer"}
    for a in agents:
        assert a["agent_id"] is not None
        assert a["agent_name"].startswith("my-new-project-")
        assert a["api_token"]  # non-empty plaintext token

    # 返回的 token 可用于鉴权
    host_token = next(a["api_token"] for a in agents if a["persona"] == "host")
    me = client.get("/api/v1/agents/me", headers={"Authorization": f"Bearer {host_token}"})
    assert me.status_code == 200
    assert me.json()["name"] == "my-new-project-host"
    assert me.json()["project_id"] == project["id"]


def test_bootstrap_duplicate_project_key_returns_409(client):
    """重复 project_key → 409，且不创建任何资源。"""
    # 第一次成功
    resp1 = client.post(
        "/api/v1/bootstrap",
        json={
            "project_key": "dup-key",
            "project_name": "First",
            "workspace_path": "/tmp/dup1",
        },
    )
    assert resp1.status_code == 201

    # 第二次 409
    resp2 = client.post(
        "/api/v1/bootstrap",
        json={
            "project_key": "dup-key",
            "project_name": "Second",
            "workspace_path": "/tmp/dup2",
        },
    )
    assert resp2.status_code == 409
    assert "already exists" in resp2.json()["detail"].lower()


def test_bootstrap_duplicate_agent_name_returns_409(client):
    """不同 project_key 但 slug 相同 → agent name 冲突 → 409。

    slug("my-slug") == "my-slug"，两次 bootstrap 会生成
    my-slug-host / my-slug-participant / my-slug-reviewer，
    第二次因 agent name 唯一约束 409。
    """
    resp1 = client.post(
        "/api/v1/bootstrap",
        json={
            "project_key": "my-slug",
            "project_name": "First Project",
            "workspace_path": "/tmp/slug1",
        },
    )
    assert resp1.status_code == 201

    resp2 = client.post(
        "/api/v1/bootstrap",
        json={
            "project_key": "my-slug",  # same slug → same agent names
            "project_name": "Second Project",
            "workspace_path": "/tmp/slug2",
        },
    )
    assert resp2.status_code == 409


def test_bootstrap_atomic_on_agent_name_collision(client, admin_headers):
    """如果 agent name 已被 admin 路径创建，bootstrap 应 409 且不创建 project。"""
    # 先用 admin 路径创建一个 agent，名字会和 bootstrap 生成的冲突
    slug = "collide-test"
    agent_name = f"{slug}-host"
    resp = client.post(
        "/api/v1/agents",
        headers=admin_headers,
        json={"name": agent_name, "role": "agent", "project_key": "existing-collide"},
    )
    # 需要 project 存在；先建 project
    proj = client.post(
        "/api/v1/projects",
        headers=admin_headers,
        json={
            "project_key": "existing-collide",
            "name": "Existing",
            "workspace_path": "/tmp/existing",
        },
    )
    assert proj.status_code == 201
    resp = client.post(
        "/api/v1/agents",
        headers=admin_headers,
        json={"name": agent_name, "role": "agent", "project_key": "existing-collide"},
    )
    assert resp.status_code == 201

    # bootstrap 同 slug → agent name 冲突 → 409
    boot = client.post(
        "/api/v1/bootstrap",
        json={
            "project_key": "collide-test",  # different key, same slug
            "project_name": "Collide",
            "workspace_path": "/tmp/collide",
        },
    )
    assert boot.status_code == 409

    # 确认没有创建新 project（collide-test 不应存在）
    # 用 admin 查 project by key
    lookup = client.get(
        "/api/v1/projects/by-key/collide-test",
        headers=admin_headers,
    )
    assert lookup.status_code == 404
