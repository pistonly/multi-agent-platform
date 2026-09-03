"""CLI 两跳 lifecycle + intent/recover 测试（实验B 24f3e565，B3~B6/B8）。

- B3：start 成功后 ``.map/intents/`` 清空；commit 失败时 intent 留存；
  ``map experiment recover`` 三分支（pending / committed / conflict / stale）。
- B4：conflict 分支输出双方证据（本地 intent vs server phase + 胜出 receipt）。
- B6：``recover --gc`` 只删 committed/stale；pending/conflict 不动。
- B8：CLI 显式携带 workspace 指纹——与 server 记录的 workspace_path 比对
  fail closed（实测 stderr/退出码），指纹匹配时成功。
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from map_client.client import MAPClient
from map_client.exceptions import MAPConflictError
from map_client.testing import MAPTestClientTransport
from map_types.schemas import ExperimentTransitionVerdict
from typer.testing import CliRunner

from cli.commands.experiment import experiment_app
from cli.experiment_transition import (
    intent_path,
    intents_dir,
    load_intents,
    save_intent,
)
from tests._frontmatter import make_valid_plan

pytestmark = pytest.mark.slow


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / ".map").mkdir()
    return tmp_path


@pytest.fixture
def map_client(client: TestClient, agent_token) -> MAPClient:
    _, token = agent_token
    c = MAPClient("http://test", token, transport=MAPTestClientTransport(client))
    yield c
    c.close()


@pytest.fixture
def patch_context(monkeypatch: pytest.MonkeyPatch, workspace: Path):
    """把 ``cli.project_context`` 的两处解析钉到测试 workspace。

    返回 ``[fingerprint]`` 列表——测试可改 ``[0]`` 控制 CLI 视角指纹
    （默认 dev=1:ino=1，与真实 stat 无关，用于不一致用例）。
    """
    from map_client.project_config import ProjectMapConfig
    from map_client.project_context import ProjectContext

    config = ProjectMapConfig(
        map_dir=workspace / ".map",
        api_url="http://test",
        project_key="test-project",
        project_id=None,
        default_persona="host",
        personas={},
        tokens={},
        content_root="map",
    )
    ctx = ProjectContext(workspace_root=workspace, config=config)
    # None → 用真实 stat（ProjectContext.workspace_fingerprint）；
    # 测试可改 [0] 伪造 CLI 视角指纹（不一致用例）。
    fingerprint_override: list[str | None] = [None]

    real_ws_fp = ProjectContext.workspace_fingerprint

    def _fp(self) -> str:
        return fingerprint_override[0] or real_ws_fp(self)

    import cli.project_context as pc

    monkeypatch.setattr(ProjectContext, "workspace_fingerprint", _fp)
    monkeypatch.setattr(pc, "optional_context", lambda: ctx)
    monkeypatch.setattr(pc, "current_context", lambda: ctx)
    monkeypatch.setattr(pc, "_cached", None)
    return fingerprint_override


@pytest.fixture
def wired_project(client: TestClient, admin_headers, project, workspace, patch_context):
    """把 server 记录的 workspace_path 指到测试 workspace（CLI 指纹可对上）。"""
    resp = client.patch(
        f"/api/v1/projects/{project['id']}",
        headers=admin_headers,
        json={"workspace_path": str(workspace)},
    )
    assert resp.status_code == 200, resp.text
    return project


@pytest.fixture
def patched_run(monkeypatch: pytest.MonkeyPatch, map_client: MAPClient):
    """``cli.runner._run`` → 同步调 ``action(map_client)``（同 T23 注入面）。

    最小复刻 ``_run`` 的 table_renderer 语义：给 renderer 时按其渲染输出。
    """
    import typer

    import cli.runner as runner_mod

    def _fake_run(action, **kwargs):
        result = action(map_client)
        renderer = kwargs.get("table_renderer")
        if renderer is not None:
            output = renderer(result)
            if output:
                typer.echo(output)
        return result

    monkeypatch.setattr(runner_mod, "_run", _fake_run)


def _runner() -> CliRunner:
    return CliRunner()


def _direct_experiment(client: TestClient, auth_headers, project, *, mode: str = "direct") -> str:
    resp = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={
            "title": "CLI 两跳实验",
            "plan": {"content_md": make_valid_plan(body="## 计划")},
            "mode": mode,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _plant_intent(
    workspace: Path,
    experiment_id: str,
    *,
    action: str = "start",
    from_phase: str = "draft",
    to_phase: str = "running",
    nonce: str | None = None,
    expired: bool = False,
) -> Path:
    verdict = ExperimentTransitionVerdict(
        token="planted-token",
        nonce=nonce or uuid.uuid4().hex,
        action=action,
        experiment_id=uuid.UUID(experiment_id),
        project_id=uuid.UUID(int=0),
        from_phase=from_phase,
        to_phase=to_phase,
        base_revision=0,
        expires_at=datetime.now(timezone.utc)
        + (timedelta(seconds=-1) if expired else timedelta(seconds=600)),
    )
    before = SimpleNamespace(phase=from_phase, current_plan_version=1, title="t")
    return save_intent(workspace, verdict, before=before, action=action, fingerprint="dev=1:ino=1")


# ---------------------------------------------------------------------------
# B3：intent 生命周期
# ---------------------------------------------------------------------------


def test_start_two_hop_cleans_intent_after_success(
    patched_run, workspace, client, auth_headers, wired_project
):
    exp_id = _direct_experiment(client, auth_headers, wired_project)
    result = _runner().invoke(experiment_app, ["start", "--id", exp_id])
    assert result.exit_code == 0, result.output
    assert (
        client.get(f"/api/v1/experiments/{exp_id}", headers=auth_headers).json()["phase"]
        == "running"
    )
    # 成功后 intent 已删
    assert load_intents(workspace, uuid.UUID(exp_id)) == []


def test_commit_failure_keeps_intent_and_recover_pending(
    patched_run, workspace, monkeypatch, map_client, client, auth_headers, wired_project
):
    ws = workspace
    exp_id = _direct_experiment(client, auth_headers, wired_project)

    # commit 阶段崩溃模拟：409（未落地任何状态）
    original_commit = MAPClient.transition_commit

    def _boom(*a, **kw):
        raise MAPConflictError(status_code=409, detail="transition CAS lost: boom")

    monkeypatch.setattr(MAPClient, "transition_commit", _boom)
    result = _runner().invoke(experiment_app, ["start", "--id", exp_id])
    assert result.exit_code != 0
    intents = load_intents(ws, uuid.UUID(exp_id))
    assert len(intents) == 1
    assert intents[0].action == "start"
    assert "recover" in result.output

    # recover（默认只读）：pending——token 未过期、server 未提交、phase 未变
    shown = _runner().invoke(experiment_app, ["recover", "--id", exp_id])
    assert shown.exit_code == 0, shown.output
    assert "[pending]" in shown.output
    assert len(load_intents(ws, uuid.UUID(exp_id))) == 1

    # B6：pending 不在 GC 范围
    gc = _runner().invoke(experiment_app, ["recover", "--id", exp_id, "--gc"])
    assert gc.exit_code == 0, gc.output
    assert len(load_intents(ws, uuid.UUID(exp_id))) == 1

    # 解除崩溃后重跑原命令：成功且 intent 清空
    monkeypatch.setattr(MAPClient, "transition_commit", original_commit)
    ok = _runner().invoke(experiment_app, ["start", "--id", exp_id])
    assert ok.exit_code == 0, ok.output
    assert load_intents(ws, uuid.UUID(exp_id)) == []


def test_recover_committed_branch_and_gc(
    patched_run, workspace, map_client, client, auth_headers, wired_project
):
    ws = workspace
    exp_id = _direct_experiment(client, auth_headers, wired_project)

    # 模拟「server 已提交但 intent 未及删除」（commit 后进程崩溃）：
    # 真实 validate+commit 一轮，然后用同 nonce 手工补一条 intent。
    verdict = map_client.transition_validate(uuid.UUID(exp_id), "start")
    map_client.transition_commit(uuid.UUID(exp_id), verdict.token)
    planted = _plant_intent(
        ws, exp_id, action="start", from_phase="draft", to_phase="running", nonce=verdict.nonce
    )

    shown = _runner().invoke(experiment_app, ["recover", "--id", exp_id])
    assert shown.exit_code == 0, shown.output
    assert "[committed]" in shown.output
    assert planted.exists()  # 默认只读

    gced = _runner().invoke(experiment_app, ["recover", "--id", exp_id, "--gc"])
    assert gced.exit_code == 0, gced.output
    assert not planted.exists()


def test_recover_conflict_branch_reports_both_sides(
    patched_run, workspace, map_client, client, auth_headers, wired_project
):
    ws = workspace
    # server 被其它 transition 推进到非终态 review；本地却留着一条
    # draft→running 的 start intent → conflict（B4 双方证据）。
    exp_id = _direct_experiment(client, auth_headers, wired_project, mode="standard")
    verdict = map_client.transition_validate(uuid.UUID(exp_id), "submit-review")
    map_client.transition_commit(uuid.UUID(exp_id), verdict.token)
    planted = _plant_intent(
        ws, exp_id, action="start", from_phase="draft", to_phase="running"
    )

    shown = _runner().invoke(experiment_app, ["recover", "--id", exp_id])
    assert shown.exit_code == 0, shown.output
    assert "[conflict]" in shown.output
    assert "phase=review" in shown.output
    assert "action=submit-review" in shown.output  # 胜出 receipt 证据
    assert "start" in shown.output  # 本地 intent 意图
    assert planted.exists()

    # B6：conflict 不被 GC（需人裁决）
    _runner().invoke(experiment_app, ["recover", "--id", exp_id, "--gc"])
    assert planted.exists()


def test_recover_stale_branch_gc_when_terminal(
    patched_run, workspace, map_client, client, auth_headers, wired_project
):
    ws = workspace
    exp_id = _direct_experiment(client, auth_headers, wired_project)
    cancel_verdict = map_client.transition_validate(uuid.UUID(exp_id), "cancel")
    map_client.transition_commit(uuid.UUID(exp_id), cancel_verdict.token)

    # 未提交过 + 实验已终结 → stale
    planted = _plant_intent(ws, exp_id, nonce="deadbeef" * 8)
    shown = _runner().invoke(experiment_app, ["recover", "--id", exp_id])
    assert shown.exit_code == 0, shown.output
    assert "[stale]" in shown.output and "已终结" in shown.output

    _runner().invoke(experiment_app, ["recover", "--id", exp_id, "--gc"])
    assert not planted.exists()


# ---------------------------------------------------------------------------
# B8：CLI 侧指纹 fail closed
# ---------------------------------------------------------------------------


def test_cli_two_hop_fingerprint_mismatch_fails_closed(
    patch_context, patched_run, map_client, client, auth_headers, project
):
    # server 记录的 workspace_path 与 CLI 本地指纹不一致 → fail closed
    patch_context[0] = "dev=999:ino=999"
    exp_id = _direct_experiment(client, auth_headers, project)

    result = _runner().invoke(experiment_app, ["start", "--id", exp_id])
    assert result.exit_code != 0
    # 不落地任何状态
    assert client.get(f"/api/v1/experiments/{exp_id}", headers=auth_headers).json()["phase"] == "draft"


def test_cli_two_hop_fingerprint_match_succeeds(
    patched_run,
    map_client,
    client,
    auth_headers,
    wired_project,
):
    # wired_project：server workspace_path == CLI workspace，指纹 = 真实
    # stat 结果 → 一致放行（B8 正常路径）。
    exp_id = _direct_experiment(client, auth_headers, wired_project)
    result = _runner().invoke(experiment_app, ["start", "--id", exp_id])
    assert result.exit_code == 0, result.output
    assert client.get(f"/api/v1/experiments/{exp_id}", headers=auth_headers).json()["phase"] == "running"


# ---------------------------------------------------------------------------
# intent 文件内容契约
# ---------------------------------------------------------------------------


def test_intent_file_carries_tuple_and_snapshot(patch_context, workspace):
    exp_id = str(uuid.uuid4())
    verdict = ExperimentTransitionVerdict(
        token="tok",
        nonce="abc",
        action="complete",
        experiment_id=uuid.UUID(exp_id),
        project_id=uuid.UUID(int=1),
        from_phase="running",
        to_phase="done",
        base_revision=3,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=600),
    )
    before = SimpleNamespace(phase="running", current_plan_version=2, title="快照")
    path = save_intent(workspace, verdict, before=before, action="complete", fingerprint="dev=2:ino=3")
    assert path == intent_path(workspace, uuid.UUID(exp_id), "done")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["token"] == "tok"
    assert data["base_revision"] == 3
    assert data["workspace_fingerprint"] == "dev=2:ino=3"
    assert data["local_snapshot"] == {
        "phase": "running",
        "current_plan_version": 2,
        "title": "快照",
    }
    assert intents_dir(workspace) == workspace / ".map" / "intents"
