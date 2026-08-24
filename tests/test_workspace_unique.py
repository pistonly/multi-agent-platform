"""3b7c2b44 A5 — workspace_path+content_root 联合键唯一性约束。

同一 workspace+content_root 被第二个 project 认领会让聚合操作二义。
命中即服务端 409 并指明已属哪个 project；不同 content_root 允许共存
（同一 repo 开多 project 合法）；update 忽略自身不误伤。
"""

from __future__ import annotations

WORKSPACE = "/tmp/ws-a5"


def _create(client, admin_headers, *, key: str, content_root: str | None = None):
    payload = {"project_key": key, "name": key, "workspace_path": WORKSPACE}
    if content_root is not None:
        payload["content_root"] = content_root
    return client.post("/api/v1/projects", headers=admin_headers, json=payload)


def test_create_same_workspace_root_409(client, admin_headers):
    assert _create(client, admin_headers, key="ws-a") .status_code == 201
    dup = _create(client, admin_headers, key="ws-b")
    assert dup.status_code == 409
    detail = dup.json().get("detail", "")
    assert "已被 project" in detail and "认领" in detail


def test_create_different_content_root_coexists(client, admin_headers):
    assert _create(client, admin_headers, key="ws-a", content_root="map").status_code == 201
    ok = _create(client, admin_headers, key="ws-b", content_root="docs")
    assert ok.status_code == 201
    assert ok.json()["content_root"] == "docs"


def test_bootstrap_same_workspace_409(client):
    first = client.post(
        "/api/v1/bootstrap",
        json={"project_key": "bt-a", "project_name": "A", "workspace_path": WORKSPACE},
    )
    assert first.status_code == 201
    dup = client.post(
        "/api/v1/bootstrap",
        json={"project_key": "bt-b", "project_name": "B", "workspace_path": WORKSPACE},
    )
    assert dup.status_code == 409
    assert "已被 project" in dup.json().get("detail", "")


def test_update_own_root_self_excluded(client, admin_headers):
    """update 保持自身联合键不变：exclude 自身，不 409。"""
    first = _create(client, admin_headers, key="ws-a").json()
    resp = client.patch(
        f"/api/v1/projects/{first['id']}",
        headers=admin_headers,
        json={"content_root": "map"},  # 本来就 map，exclude 自身
    )
    assert resp.status_code == 200


def test_update_to_occupied_root_409(client, admin_headers, project):
    occupied = _create(client, admin_headers, key="ws-occ", content_root="map").json()
    assert occupied["workspace_path"] == WORKSPACE
    # 把 project fixture（默认 /tmp/test-project）改到已占用组合 → 409
    resp = client.patch(
        f"/api/v1/projects/{project['id']}",
        headers=admin_headers,
        json={"workspace_path": WORKSPACE},
    )
    assert resp.status_code == 409
    assert "已被 project" in resp.json().get("detail", "")
