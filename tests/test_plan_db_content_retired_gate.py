"""plan_db_content_retired flag 门禁端到端测试（实验 plan-db-content-retirement A1-3）。

覆盖验收 A1-3 的两个行为面（evidence keys）：

- ``flag_plan_db_content_retired_on_reject_inline_create``：flag on 时
  内联 ``plan.content_md`` 创建 → 409 + error code + 自助化文案（指向
  --plan-file-path / materialize）。
- ``flag_on_slim_create_still_ok``：flag on 时 slim（file_path，无
  content_md）创建仍成功，DB 存 stub 的现状保留（防 A：门禁判据是
  「请求体是否携带全文」，不是「是否走 create 入口」）。
- flag off / 未 set：内联创建行为与现状一致（全量回归的第 1 条锚点）。

全部走 FastAPI TestClient 完整 HTTP 路径，不绕开 API 层。
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from tests._frontmatter import make_valid_plan


def _create_project_with_host(
    client: TestClient, admin_headers: dict[str, str]
) -> tuple[dict, dict[str, str]]:
    project_resp = client.post(
        "/api/v1/projects",
        headers=admin_headers,
        json={
            "project_key": f"planretire-{uuid.uuid4().hex[:6]}",
            "name": "PlanRetire Test",
            "workspace_path": "/tmp/planretire-test",
        },
    )
    assert project_resp.status_code == 201, project_resp.text
    project = project_resp.json()

    host_resp = client.post(
        "/api/v1/agents",
        headers=admin_headers,
        json={
            "name": f"{project['project_key']}-host",
            "role": "agent",
            "project_key": project["project_key"],
        },
    )
    assert host_resp.status_code == 201, host_resp.text
    host_headers = {"Authorization": f"Bearer {host_resp.json()['api_token']}"}
    return project, host_headers


def _set_flag(
    client: TestClient,
    host_headers: dict[str, str],
    project_id: str,
    value: str,
) -> None:
    resp = client.put(
        f"/api/v1/projects/{project_id}/feature-flags/plan_db_content_retired",
        headers=host_headers,
        json={"flag_value": value, "reason": "A1-3 gate 测试"},
    )
    assert resp.status_code == 200, resp.text


def _create_inline(
    client: TestClient, headers: dict[str, str], project_id: str
):
    return client.post(
        f"/api/v1/projects/{project_id}/experiments",
        headers=headers,
        json={
            "title": "A1-3 门禁实验（内联）",
            "plan": {"content_md": make_valid_plan(body="## 目标\nA1-3 gate")},
        },
    )


def _create_slim(
    client: TestClient, headers: dict[str, str], project_id: str
):
    return client.post(
        f"/api/v1/projects/{project_id}/experiments",
        headers=headers,
        json={
            "title": "A1-3 门禁实验（slim）",
            "plan": {"file_path": "map/experiments/a1-3-slim/plan.md"},
            "plan_file_path": "map/experiments/a1-3-slim/plan.md",
        },
    )


# ---------------------------------------------------------------------------
# flag on：拒内联 / 放 slim
# ---------------------------------------------------------------------------


def test_flag_on_rejects_inline_create_with_self_service_409(
    client: TestClient, admin_headers: dict[str, str]
):
    """evidence: flag_plan_db_content_retired_on_reject_inline_create。"""
    project, host_headers = _create_project_with_host(client, admin_headers)
    _set_flag(client, host_headers, project["id"], "on")

    resp = _create_inline(client, host_headers, project["id"])
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    text = detail if isinstance(detail, str) else str(detail)
    # 自助化：两条出路都点名，报错自带指引。
    assert "--plan-file-path" in text
    assert "plan materialize" in text

    # 未落库：拒绝必须先于任何 DB 写入。
    listed = client.get(
        f"/api/v1/projects/{project['id']}/experiments", headers=host_headers
    ).json()
    assert listed == [] or all(
        e["title"] != "A1-3 门禁实验（内联）" for e in listed
    )


def test_flag_on_slim_create_still_ok(
    client: TestClient, admin_headers: dict[str, str]
):
    """evidence: flag_on_slim_create_still_ok —— slim 分支放行，stub 现状保留。"""
    project, host_headers = _create_project_with_host(client, admin_headers)
    _set_flag(client, host_headers, project["id"], "on")

    resp = _create_slim(client, host_headers, project["id"])
    assert resp.status_code == 201, resp.text
    created = resp.json()
    assert created["plan_file_path"] == "map/experiments/a1-3-slim/plan.md"
    # 现状保留：content_md NOT NULL → 自描述 stub（slim create 契约）。
    detail = client.get(
        f"/api/v1/experiments/{created['id']}", headers=host_headers
    ).json()
    content = detail["current_plan"]["content_md"]
    assert content.startswith("<!-- slim create")
    assert "map/experiments/a1-3-slim/plan.md" in content


# ---------------------------------------------------------------------------
# flag off / 未 set：行为与现状一致
# ---------------------------------------------------------------------------


def test_flag_off_inline_create_unchanged(
    client: TestClient, admin_headers: dict[str, str]
):
    """flag off（显式）：内联创建仍成功写全文——门禁零侵入。"""
    project, host_headers = _create_project_with_host(client, admin_headers)
    _set_flag(client, host_headers, project["id"], "off")

    resp = _create_inline(client, host_headers, project["id"])
    assert resp.status_code == 201, resp.text
    detail = client.get(
        f"/api/v1/experiments/{resp.json()['id']}", headers=host_headers
    ).json()
    assert "A1-3 gate" in detail["current_plan"]["content_md"]


def test_flag_unset_inline_create_unchanged(
    client: TestClient, admin_headers: dict[str, str]
):
    """flag 从未 set（保守默认 off）：内联创建行为与现状逐字节一致。"""
    project, host_headers = _create_project_with_host(client, admin_headers)

    resp = _create_inline(client, host_headers, project["id"])
    assert resp.status_code == 201, resp.text
    detail = client.get(
        f"/api/v1/experiments/{resp.json()['id']}", headers=host_headers
    ).json()
    assert "A1-3 gate" in detail["current_plan"]["content_md"]


def test_kill_switch_reopens_inline_create(
    client: TestClient, admin_headers: dict[str, str]
):
    """kill switch：on 拒绝 → flip off → 同一请求恢复成功（fast rollback）。"""
    project, host_headers = _create_project_with_host(client, admin_headers)
    _set_flag(client, host_headers, project["id"], "on")
    assert _create_inline(client, host_headers, project["id"]).status_code == 409

    _set_flag(client, host_headers, project["id"], "off")
    assert _create_inline(client, host_headers, project["id"]).status_code == 201
