"""plan v3（A1–A7）:action-items.yaml 生命周期——解析 / 投影路由 / close 门禁 / 投影缓存回退 / CLI 写回。

实验 3d519184「FS close_note 的 action_items 解析入跟踪」v3 路线：
收敛时 host 落盘 action-items.yaml → owner 在 work 义务（kind=action_items）督促下
complete（带证据）/ cancel（带理由）→ close 门禁校验零 open 才放行（closed = 零尾款）。
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
import yaml
from map_fs import (
    FsActionItem,
    parse_action_items_file,
    scan_plane,
    topic_id_for_slug,
    write_action_items,
    write_round_comment,
    write_topic_index,
)
from typer.testing import CliRunner

runner = CliRunner()


def _ai_path(tmp_path: Path, slug: str) -> Path:
    return tmp_path / "map" / "topics" / slug / "action-items.yaml"


# ---------------------------------------------------------------------------
# A1: action-items.yaml 解析（map_fs parser）
# ---------------------------------------------------------------------------


def test_action_items_roundtrip(tmp_path: Path) -> None:
    write_topic_index(tmp_path, "ai-demo", title="AI Demo", creator="host")
    write_round_comment(tmp_path, "ai-demo", round_number=1, persona="host", body="# r1")
    write_action_items(
        tmp_path,
        "ai-demo",
        [
            FsActionItem(id=1, title="合并主分支", owner="participant", status="open"),
            FsActionItem(
                id=2,
                title="同步文档",
                owner="host",
                status="done",
                evidence="6aaca4c",
            ),
            FsActionItem(
                id=3,
                title="不做了",
                owner="participant",
                status="cancelled",
                reason="需求取消",
            ),
        ],
    )

    topic = scan_plane(tmp_path).topic_by_slug("ai-demo")
    assert topic is not None
    assert topic.action_items_error is None
    assert [item.id for item in topic.action_items] == [1, 2, 3]
    item1 = next(i for i in topic.action_items if i.id == 1)
    assert item1.owner == "participant"
    assert item1.status == "open"
    item2 = next(i for i in topic.action_items if i.id == 2)
    assert item2.status == "done"
    assert item2.evidence == "6aaca4c"
    item3 = next(i for i in topic.action_items if i.id == 3)
    assert item3.status == "cancelled"
    assert item3.reason == "需求取消"
    # 空列表也落盘空文件（存在即接管执行项）
    write_action_items(tmp_path, "ai-demo", [])
    assert _ai_path(tmp_path, "ai-demo").is_file()
    topic = scan_plane(tmp_path).topic_by_slug("ai-demo")
    assert topic is not None and topic.action_items == []


def test_action_items_missing_file_means_empty(tmp_path: Path) -> None:
    write_topic_index(tmp_path, "ai-none", title="T", creator="host")
    topic = scan_plane(tmp_path).topic_by_slug("ai-none")
    assert topic is not None
    assert topic.action_items == []
    assert topic.action_items_error is None


def test_action_items_handwritten_without_ids(tmp_path: Path) -> None:
    """手写友好：省略 id 时按文档顺序 1-based 兜底。"""
    path = _ai_path(tmp_path, "ai-hand")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "- title: 第一项\n  owner: participant\n  status: open\n"
        "- title: 第二项\n  owner: host\n  status: done\n  evidence: xx\n",
        encoding="utf-8",
    )
    items, error = parse_action_items_file(path)
    assert error is None
    assert [(item.id, item.title) for item in items] == [(1, "第一项"), (2, "第二项")]


@pytest.mark.parametrize(
    ("content", "substr"),
    [
        (": bad: [yaml", "不是合法 YAML"),  # YAMLError
        ("foo: bar\n", "应为列表"),  # dict 而非列表
        ("- title: x\n  owner: host\n  status: nope\n", "status"),  # status 非法
        ("- owner: host\n  status: open\n", "title"),  # title 缺失
        ("- title: x\n  status: open\n", "owner"),  # owner 缺失
        (
            "- id: 1\n  title: a\n  owner: host\n  status: open\n"
            "- id: 1\n  title: b\n  owner: host\n  status: done\n",
            "重复 id",
        ),  # id 重复
    ],
)
def test_action_items_malformed_not_silent(tmp_path: Path, content: str, substr: str) -> None:
    """格式错漏 → (空列表, 错误文案)——不静默（A1），close 门禁据此 409。"""
    write_topic_index(tmp_path, "ai-bad", title="Bad", creator="host")
    path = _ai_path(tmp_path, "ai-bad")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    items, error = parse_action_items_file(path)
    assert items == []
    assert error is not None and substr in error
    topic = scan_plane(tmp_path).topic_by_slug("ai-bad")
    assert topic is not None and topic.action_items_error == error


# ---------------------------------------------------------------------------
# API helpers（对齐 test_fs_source.py 约定）
# ---------------------------------------------------------------------------


def _create_project(client, admin_headers: dict, tmp_path: Path) -> dict:
    response = client.post(
        "/api/v1/projects",
        headers=admin_headers,
        json={
            "project_key": f"fs-ai-{uuid.uuid4().hex[:8]}",
            "name": "FS AI Test Project",
            "workspace_path": str(tmp_path),
        },
    )
    assert response.status_code == 201
    return response.json()


def _make_agent(client, admin_headers: dict, project: dict, name: str) -> dict:
    resp = client.post(
        "/api/v1/agents",
        headers=admin_headers,
        json={"name": name, "role": "agent", "project_key": project["project_key"]},
    )
    assert resp.status_code == 201
    return {"Authorization": f"Bearer {resp.json()['api_token']}"}


def _work_kinds(client, headers: dict, slug: str) -> list[dict]:
    """该话题在 work 快照里的 (kind, excerpt) 列表。"""
    resp = client.get("/api/v1/agents/me/work", headers=headers)
    assert resp.status_code == 200
    return [
        {"kind": w["kind"], "excerpt": w.get("excerpt") or ""}
        for item in resp.json()["topic_progress"]["items"]
        if item["topic_id"] == str(topic_id_for_slug(slug))
        for w in item["work_items"]
    ]


# ---------------------------------------------------------------------------
# A3: close 门禁（唯一防线，closed = 零尾款）
# ---------------------------------------------------------------------------


def test_fs_close_gate_blocks_open_then_passes_after_clear(
    client, admin_headers: dict, tmp_path: Path
) -> None:
    project = _create_project(client, admin_headers, tmp_path)
    pid = project["id"]
    write_topic_index(tmp_path, "ai-gate", title="Gate", creator="host")
    write_round_comment(tmp_path, "ai-gate", round_number=1, persona="host", body="# r1")
    write_action_items(
        tmp_path,
        "ai-gate",
        [
            FsActionItem(id=1, title="合并主分支", owner="participant", status="open"),
            FsActionItem(id=2, title="同步文档", owner="participant", status="done", evidence="xx"),
        ],
    )

    resp = client.post(
        f"/api/v1/projects/{pid}/fs/topics/ai-gate/close", headers=admin_headers
    )
    assert resp.status_code == 409
    body = resp.json()["detail"]
    assert body["error"] == "action_items_open"
    assert any(item["id"] == 1 and item["owner"] == "participant" for item in body["items"])
    assert all(item["id"] in (1,) for item in body["items"])  # done 项不拦

    # 清零 #1 → 门禁放行
    write_action_items(
        tmp_path,
        "ai-gate",
        [
            FsActionItem(id=1, title="合并主分支", owner="participant", status="done", evidence="6aaca4c"),
            FsActionItem(id=2, title="同步文档", owner="participant", status="done", evidence="xx"),
        ],
    )
    resp = client.post(
        f"/api/v1/projects/{pid}/fs/topics/ai-gate/close", headers=admin_headers
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "closed"


def test_fs_close_gate_allows_cancelled_with_reason(
    client, admin_headers: dict, tmp_path: Path
) -> None:
    """cancel 显式放弃不挡 close（A5：审计留痕，零尾款达成）。"""
    project = _create_project(client, admin_headers, tmp_path)
    pid = project["id"]
    write_topic_index(tmp_path, "ai-cancel", title="Cancel", creator="host")
    write_round_comment(tmp_path, "ai-cancel", round_number=1, persona="host", body="# r1")
    write_action_items(
        tmp_path,
        "ai-cancel",
        [FsActionItem(id=1, title="不做了", owner="host", status="cancelled", reason="需求取消")],
    )
    resp = client.post(
        f"/api/v1/projects/{pid}/fs/topics/ai-cancel/close", headers=admin_headers
    )
    assert resp.status_code == 200


def test_fs_close_gate_blocks_malformed_yaml(
    client, admin_headers: dict, tmp_path: Path
) -> None:
    """yaml 格式错漏无法判定是否零尾款 → 同样拦 close（A1 不静默）。"""
    project = _create_project(client, admin_headers, tmp_path)
    pid = project["id"]
    write_topic_index(tmp_path, "ai-malformed", title="Malformed", creator="host")
    write_round_comment(tmp_path, "ai-malformed", round_number=1, persona="host", body="# r1")
    path = _ai_path(tmp_path, "ai-malformed")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("- title: x\n  status: open\n", encoding="utf-8")  # owner 缺失

    resp = client.post(
        f"/api/v1/projects/{pid}/fs/topics/ai-malformed/close", headers=admin_headers
    )
    assert resp.status_code == 409
    body = resp.json()["detail"]
    assert body["error"] == "action_items_open"
    assert body["items"] == []
    assert "owner" in body["detail"]


# ---------------------------------------------------------------------------
# A2: 投影义务——owner persona 精确路由，done 不投影
# ---------------------------------------------------------------------------


def test_fs_action_items_projection_routes_to_owner(
    client, admin_headers: dict, tmp_path: Path
) -> None:
    project = _create_project(client, admin_headers, tmp_path)
    pid = project["id"]
    part_headers = _make_agent(client, admin_headers, project, "fs-participant")
    host_headers = _make_agent(client, admin_headers, project, "fs-host")
    other_headers = _make_agent(client, admin_headers, project, "fs-other")

    write_topic_index(tmp_path, "ai-wake", title="Wake", creator="host")
    write_round_comment(tmp_path, "ai-wake", round_number=1, persona="host", body="# r1")
    write_action_items(
        tmp_path,
        "ai-wake",
        [
            FsActionItem(id=1, title="写文档", owner="participant", status="open"),
            FsActionItem(id=2, title="host 事项", owner="host", status="open"),
            FsActionItem(id=3, title="已完成", owner="participant", status="done", evidence="xx"),
        ],
    )

    part_kinds = _work_kinds(client, part_headers, "ai-wake")
    assert [k for k in part_kinds if k["kind"] == "action_items"] == [
        {"kind": "action_items", "excerpt": "行动项 #1: 写文档"}
    ]
    host_kinds = _work_kinds(client, host_headers, "ai-wake")
    assert [k for k in host_kinds if k["kind"] == "action_items"] == [
        {"kind": "action_items", "excerpt": "行动项 #2: host 事项"}
    ]
    other_kinds = _work_kinds(client, other_headers, "ai-wake")
    assert not any(k["kind"] == "action_items" for k in other_kinds)

    # 清零 host 自己的 #2 → host 的义务消失
    write_action_items(
        tmp_path,
        "ai-wake",
        [
            FsActionItem(id=2, title="host 事项", owner="host", status="done", evidence="ok"),
            FsActionItem(id=3, title="已完成", owner="participant", status="done", evidence="xx"),
        ],
    )
    host_kinds = _work_kinds(client, host_headers, "ai-wake")
    assert not any(k["kind"] == "action_items" for k in host_kinds)
    # #1 也带证据完成 → 全清零 → close 放行；close 后 topic 移出投影（只对 open 话题投影）
    write_action_items(
        tmp_path,
        "ai-wake",
        [
            FsActionItem(id=1, title="写文档", owner="participant", status="done", evidence="ok"),
            FsActionItem(id=2, title="host 事项", owner="host", status="done", evidence="ok"),
            FsActionItem(id=3, title="已完成", owner="participant", status="done", evidence="xx"),
        ],
    )
    resp = client.post(
        f"/api/v1/projects/{pid}/fs/topics/ai-wake/close", headers=admin_headers
    )
    assert resp.status_code in (200, 201)
    assert not any(k["kind"] == "action_items" for k in _work_kinds(client, part_headers, "ai-wake"))


# ---------------------------------------------------------------------------
# A7: 投影缓存回退（远程部署）——action-items.yaml 随 push 上行、门禁读缓存同源
# ---------------------------------------------------------------------------


def test_action_items_project_from_projection_cache(
    client, admin_headers: dict, tmp_path: Path
) -> None:
    """server 看不到 workspace 时,action-items.yaml 随 map fs push 入投影缓存;
    work 投影与 close/validate 门禁都读缓存（A7 读路径同源）。"""
    from map_fs import scan_plane

    from cli.commands.fs import fs_topic_to_detail_read

    client_ws = tmp_path / "ai-client-ws"
    client_ws.mkdir()
    write_topic_index(
        client_ws,
        "ai-remote",
        title="AI Remote",
        creator="host",
        participants=["participant"],
    )
    write_round_comment(client_ws, "ai-remote", round_number=1, persona="host", body="# r1")
    write_action_items(
        client_ws,
        "ai-remote",
        [FsActionItem(id=1, title="合并主分支", owner="participant", status="open")],
    )

    project = client.post(
        "/api/v1/projects",
        headers=admin_headers,
        json={
            "project_key": f"fs-ai-{uuid.uuid4().hex[:8]}",
            "name": "AI Remote Project",
            "workspace_path": str(tmp_path / "ai-no-view"),
        },
    ).json()
    pid = project["id"]

    def _push(base_revision: int | None = None) -> int:
        plane = scan_plane(client_ws)
        resp = client.put(
            f"/api/v1/projects/{pid}/fs/projection",
            headers=admin_headers,
            json={
                "client_workspace": str(client_ws),
                "base_revision": base_revision,
                "topics": [fs_topic_to_detail_read(t).model_dump(mode="json") for t in plane.topics],
                "experiments": [],
            },
        )
        assert resp.status_code == 200
        return resp.json()["projection_revision"]

    revision = _push()

    part = client.post(
        "/api/v1/agents",
        headers=admin_headers,
        json={
            "name": "multi-agent-platform-participant",
            "role": "agent",
            "project_key": project["project_key"],
        },
    ).json()
    part_headers = {"Authorization": f"Bearer {part['api_token']}"}

    kinds = _work_kinds(client, part_headers, "ai-remote")
    assert any(k["kind"] == "action_items" for k in kinds)

    # 远程门禁读缓存：open #1 → validate 409 action_items_open
    resp = client.post(
        f"/api/v1/projects/{pid}/fs/topics/ai-remote/close/validate",
        headers=admin_headers,
        json={"base_revision": revision},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "action_items_open"

    # 客户端完成 #1 并重推 → 门禁放行
    write_action_items(
        client_ws,
        "ai-remote",
        [FsActionItem(id=1, title="合并主分支", owner="participant", status="done", evidence="6aaca4c")],
    )
    revision = _push(revision)
    resp = client.post(
        f"/api/v1/projects/{pid}/fs/topics/ai-remote/close/validate",
        headers=admin_headers,
        json={"base_revision": revision},
    )
    assert resp.status_code == 200
    assert resp.json()["allowed"] is True


# ---------------------------------------------------------------------------
# A4/A5: CLI（本地写回 action-items.yaml）——纯本地，--no-sync 避免触碰 server
# ---------------------------------------------------------------------------


@pytest.fixture()
def ai_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    map_dir = tmp_path / ".map"
    map_dir.mkdir(parents=True, exist_ok=True)
    (map_dir / "config.yaml").write_text(
        yaml.safe_dump({"api_url": "http://localhost:8001", "project_key": "test"}),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    write_topic_index(tmp_path, "ai-cli", title="CLI", creator="host")
    return tmp_path


def _invoke(*args: str) -> Any:
    from cli.commands.topic import action_item_app

    return runner.invoke(action_item_app, list(args))


def test_cli_add_then_complete_with_evidence(ai_workspace: Path) -> None:
    result = _invoke("add", "--topic", "ai-cli", "--owner", "participant", "--title", "写文档", "--no-sync")
    assert result.exit_code == 0, result.output
    path = _ai_path(ai_workspace, "ai-cli")
    items, error = parse_action_items_file(path)
    assert error is None and len(items) == 1
    assert items[0].id == 1 and items[0].status == "open"

    result = _invoke("complete", "--topic", "ai-cli", "--id", "1", "--evidence", "6aaca4c", "--no-sync")
    assert result.exit_code == 0, result.output
    items, error = parse_action_items_file(path)
    assert error is None
    assert items[0].status == "done" and items[0].evidence == "6aaca4c"

    # done 后再次 complete → 拒绝（open → done/cancelled 单向）
    result = _invoke("complete", "--topic", "ai-cli", "--id", "1", "--evidence", "again", "--no-sync")
    assert result.exit_code == 1


def test_cli_complete_rejects_empty_evidence(ai_workspace: Path) -> None:
    result = _invoke("complete", "--topic", "ai-cli", "--id", "1", "--evidence", "  ", "--no-sync")
    assert result.exit_code == 2
    assert "evidence 必填" in result.output
    # 未写任何文件
    assert not _ai_path(ai_workspace, "ai-cli").exists()


def test_cli_cancel_with_reason(ai_workspace: Path) -> None:
    write_action_items(
        ai_workspace,
        "ai-cli",
        [FsActionItem(id=7, title="不做了", owner="host", status="open")],
    )
    result = _invoke("cancel", "--topic", "ai-cli", "--id", "7", "--reason", "需求取消", "--no-sync")
    assert result.exit_code == 0, result.output
    items, error = parse_action_items_file(_ai_path(ai_workspace, "ai-cli"))
    assert error is None
    item = next(i for i in items if i.id == 7)
    assert item.status == "cancelled" and item.reason == "需求取消"


def test_cli_rejects_writes_on_malformed_yaml(ai_workspace: Path) -> None:
    """格式错漏时 CLI 写回也拒绝（A1 不静默，避免覆盖手写内容）。"""
    path = _ai_path(ai_workspace, "ai-cli")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("- title: x\n  status: open\n", encoding="utf-8")  # owner 缺失
    result = _invoke("complete", "--topic", "ai-cli", "--id", "1", "--evidence", "ok", "--no-sync")
    assert result.exit_code == 1
    assert path.read_text(encoding="utf-8") == "- title: x\n  status: open\n"


def test_cli_action_item_list_and_not_found(ai_workspace: Path) -> None:
    write_action_items(
        ai_workspace,
        "ai-cli",
        [FsActionItem(id=1, title="写文档", owner="participant", status="open")],
    )
    result = _invoke("list", "--topic", "ai-cli")
    assert result.exit_code == 0
    assert "#1 [open] 写文档" in result.output

    result = _invoke("complete", "--topic", "ai-cli", "--id", "99", "--evidence", "x", "--no-sync")
    assert result.exit_code == 1
    assert "不存在" in result.output


def test_render_validate_error_action_items_open(capsys: pytest.CaptureFixture) -> None:
    """close 409 action_items_open → CLI 渲染列出未清零项并引导 complete/cancel。"""
    from types import SimpleNamespace

    import cli.commands.fs as fs_cli

    exc = SimpleNamespace(
        status_code=409,
        detail={
            "error": "action_items_open",
            "items": [{"id": 1, "title": "合并主分支", "owner": "participant"}],
            "detail": "",
        },
    )
    fs_cli._render_validate_error(exc)  # type: ignore[arg-type]
    out = capsys.readouterr().err
    assert "零尾款" in out
    assert "#1 合并主分支" in out
    assert "complete/cancel" in out

    # 格式错漏形态：items 为空、detail 带原因
    exc = SimpleNamespace(
        status_code=409,
        detail={"error": "action_items_open", "items": [], "detail": "action-items.yaml 不是合法 YAML: x"},
    )
    fs_cli._render_validate_error(exc)  # type: ignore[arg-type]
    assert "无法解析" in capsys.readouterr().err

    # live 传输形态：map_client 把 detail str() 掉 → 字符串化 dict 也要能渲染
    exc = SimpleNamespace(
        status_code=409,
        detail="{'error': 'action_items_open', 'items': [{'id': 2, 'title': '合并主分支', 'owner': 'participant'}], 'detail': ''}",
    )
    fs_cli._render_validate_error(exc)  # type: ignore[arg-type]
    out = capsys.readouterr().err
    assert "零尾款" in out
    assert "#2 合并主分支" in out
