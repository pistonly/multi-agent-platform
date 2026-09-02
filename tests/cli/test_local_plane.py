"""local plane（``plane: local``）CLI 离线全流程。

零注册/零 token/零网络：全程 monkeypatch ``cli.main.resolve_client`` 为
raise，任何建客户端的路径都会让测试炸掉。``map_client.project_config.
find_map_dir`` 也打桩到 tmp workspace（cwd 是本仓库，不能串到真 .map/）。
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import map_client.project_config as pc
import pytest
import yaml
from typer.testing import CliRunner

import cli.commands.fs as fs_cli
import cli.main
from cli.main import app


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def local_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """tmp workspace：.map/config.yaml（plane: local + uuid4）+ 三个 persona。"""
    map_dir = tmp_path / ".map"
    map_dir.mkdir()
    (map_dir / "config.yaml").write_text(
        "api_url: http://localhost:1\n"
        "project_key: localtest\n"
        f"project_id: {uuid.uuid4()}\n"
        "plane: local\n"
        "default_persona: host\n"
        "content_root: map\n",
        encoding="utf-8",
    )
    (map_dir / "agents.yaml").write_text(
        "personas:\n"
        "  host:\n    agent_name: localtest-host\n"
        "  participant:\n    agent_name: localtest-participant\n"
        "  reviewer:\n    agent_name: localtest-reviewer\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(fs_cli, "_workspace", lambda: tmp_path)
    monkeypatch.setattr(pc, "find_map_dir", lambda start=None: map_dir)
    # 实验 e7244a91（A1）：context 单点解析经 cli.main 注入面读 find_map_dir，
    # 与 pc.find_map_dir 一并钉住，避免解析回真实仓库 .map/。
    monkeypatch.setattr(cli.main, "find_map_dir", lambda start=None: map_dir)

    # 零客户端守卫：local plane 命令若误建客户端，会拿到与真实
    # resolve_client 一致的 ValueError（→ exit 1），断言 exit 0 的测试即失败；
    # 同时 unsupported-command 测试能断言真实的 plane: local 提示文案。
    def _no_client(**kwargs):
        raise ValueError(pc.LOCAL_PLANE_HINT)

    monkeypatch.setattr(cli.main, "resolve_client", _no_client)
    monkeypatch.setattr(
        cli.main, "admin_client", lambda *a, **k: (_ for _ in ()).throw(_no_client())
    )
    return tmp_path


def _create(runner: CliRunner, slug: str, title: str, participants: str | None = None) -> None:
    args = ["topic", "create", "--slug", slug, "--title", title]
    if participants:
        args += ["--participants", participants]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output


def _comment(runner: CliRunner, slug: str, persona: str, body: str = "观点") -> None:
    result = runner.invoke(
        app,
        ["--persona", persona, "topic", "comment", "--topic", slug, "--body", body],
    )
    assert result.exit_code == 0, result.output


def _index(ws: Path, slug: str) -> dict:
    text = (ws / "map" / "topics" / slug / "index.md").read_text(encoding="utf-8")
    return yaml.safe_load(text.split("---")[1])


def test_bootstrap_local_offline(runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def _no_client(**kwargs):
        raise AssertionError("bootstrap --local must not touch the server")

    monkeypatch.setattr(cli.main, "resolve_client", _no_client)
    monkeypatch.setattr(
        cli.main, "admin_client", lambda *a, **k: (_ for _ in ()).throw(_no_client())
    )
    result = runner.invoke(
        app,
        [
            "bootstrap",
            "--local",
            "--key",
            "localtest",
            "--project-root",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    cfg = yaml.safe_load((tmp_path / ".map" / "config.yaml").read_text(encoding="utf-8"))
    assert cfg["plane"] == "local"
    uuid.UUID(cfg["project_id"])  # 手写 uuid4，永不注册
    assert cfg["default_persona"] == "host"
    assert not (tmp_path / ".map" / "agents.local.yaml").exists()
    agents = yaml.safe_load((tmp_path / ".map" / "agents.yaml").read_text(encoding="utf-8"))
    assert set(agents["personas"]) == {"host", "participant", "reviewer"}
    assert agents["personas"]["host"]["agent_name"] == "localtest-host"
    # 内容根已建
    assert (tmp_path / "map" / "topics").is_dir()
    assert (tmp_path / "map" / "experiments").is_dir()
    # 重复 bootstrap 拒绝
    again = runner.invoke(
        app, ["bootstrap", "--local", "--key", "localtest", "--project-root", str(tmp_path)]
    )
    assert again.exit_code == 1
    assert "已存在" in again.output


def test_full_offline_lifecycle(runner: CliRunner, local_workspace: Path) -> None:
    ws = local_workspace
    _create(runner, "t1", "Noise review", "participant,reviewer")
    index = ws / "map" / "topics" / "t1" / "index.md"

    # ack 未满（无人发言）→ 拦截且 index 字节不变
    before = index.read_bytes()
    result = runner.invoke(app, ["topic", "advance-round", "--topic", "t1"])
    assert result.exit_code == 1
    assert "round ack pending" in result.output
    assert "- participant" in result.output
    assert "- reviewer" in result.output
    assert index.read_bytes() == before

    # 全员发言 → advance 成功
    _comment(runner, "t1", "participant")
    _comment(runner, "t1", "reviewer")
    result = runner.invoke(app, ["topic", "advance-round", "--topic", "t1"])
    assert result.exit_code == 0, result.output
    assert _index(ws, "t1")["round"] == "round2"

    # close（creator=host，无 action items）
    result = runner.invoke(
        app,
        [
            "topic", "close", "--topic", "t1",
            "--reason", "discussion_converged",
            "--note", "实验讨论收敛\n\nexperiment_id: none\nfollowup_gate: 无实验直接归档",
        ],
    )
    assert result.exit_code == 0, result.output
    idx = _index(ws, "t1")
    assert idx["status"] == "closed"
    assert idx["close_reason"] == "discussion_converged"

    # audit.jsonl 两行：advance-round → close，均为 local-plane 来源
    audit_path = ws / "map" / "topics" / "t1" / "audit.jsonl"
    lines = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert [line["action"] for line in lines] == ["advance-round", "close"]
    assert all(line["source"] == "local-plane" for line in lines)
    assert lines[0]["actor_persona"] == "host"
    assert lines[0]["fields"] == {"round": "round2"}


def test_advance_waive_and_ready(runner: CliRunner, local_workspace: Path) -> None:
    ws = local_workspace
    _create(runner, "t1", "W", "participant")
    result = runner.invoke(
        app,
        [
            "topic",
            "advance-round",
            "--topic",
            "t1",
            "--waive-ack",
            "--waive-reason",
            "single-speaker",
        ],
    )
    assert result.exit_code == 0, result.output
    idx = _index(ws, "t1")
    assert idx["round"] == "round2"
    assert idx["waive_reason"] == "single-speaker"

    _create(runner, "t2", "R", "participant")
    _comment(runner, "t2", "participant")
    result = runner.invoke(app, ["topic", "advance-round", "--topic", "t2", "--ready"])
    assert result.exit_code == 0, result.output
    assert _index(ws, "t2")["round"] == "ready"


def test_close_action_items_gate(runner: CliRunner, local_workspace: Path) -> None:
    from map_fs import FsActionItem, write_action_items

    ws = local_workspace
    _create(runner, "t1", "A")
    write_action_items(
        ws, "t1", [FsActionItem(id=1, title="inspect pump", owner="host", status="open")]
    )
    result = runner.invoke(app, ["topic", "close", "--topic", "t1"])
    assert result.exit_code == 1
    assert "action items 未清零" in result.output
    assert "#1 inspect pump" in result.output

    write_action_items(
        ws,
        "t1",
        [FsActionItem(id=1, title="inspect pump", owner="host", status="done", evidence="report")],
    )
    result = runner.invoke(app, ["topic", "close", "--topic", "t1"])
    assert result.exit_code == 0, result.output


def test_owner_gate_non_creator(runner: CliRunner, local_workspace: Path) -> None:
    _create(runner, "t1", "O", "participant")
    result = runner.invoke(
        app,
        [
            "--persona",
            "participant",
            "topic",
            "advance-round",
            "--topic",
            "t1",
            "--waive-ack",
            "--waive-reason",
            "x",
        ],
    )
    assert result.exit_code == 1
    assert "only the topic creator 'host' can advance-round" in result.output


def test_topic_ref_resolution(runner: CliRunner, local_workspace: Path) -> None:
    from map_fs import parse_topic_dir

    ws = local_workspace
    _create(runner, "t1", "Ref")
    result = runner.invoke(app, ["topic", "advance-round", "--topic", "nope"])
    assert result.exit_code == 1
    assert "not a local topic" in result.output

    topic = parse_topic_dir(ws / "map" / "topics" / "t1", ws)
    result = runner.invoke(app, ["topic", "close", "--topic", str(topic.id)])
    assert result.exit_code == 0, result.output
    assert _index(ws, "t1")["status"] == "closed"


def test_topic_list_local_only(runner: CliRunner, local_workspace: Path) -> None:
    _create(runner, "t1", "Noise alpha")
    _create(runner, "t2", "Beta check")
    result = runner.invoke(app, ["topic", "close", "--topic", "t2"])
    assert result.exit_code == 0, result.output

    # 表格列展示短 id（非 slug），按 title 断言
    result = runner.invoke(app, ["topic", "list"])
    assert result.exit_code == 0, result.output
    assert "Noise alpha" in result.output and "Beta check" in result.output

    result = runner.invoke(app, ["topic", "list", "--status", "closed"])
    assert result.exit_code == 0, result.output
    assert "Beta check" in result.output and "Noise alpha" not in result.output

    result = runner.invoke(app, ["topic", "list", "--q", "noise"])
    assert result.exit_code == 0, result.output
    assert "Noise alpha" in result.output and "Beta check" not in result.output


def test_unsupported_command_hint(runner: CliRunner, local_workspace: Path) -> None:
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 1
    assert "plane: local" in result.output
    assert "requires a MAP server" in result.output


def test_doctor_and_local_read_commands(runner: CliRunner, local_workspace: Path) -> None:
    _create(runner, "t1", "D")
    result = runner.invoke(app, ["doctor", "config"])
    assert result.exit_code == 0, result.output
    assert "plane: local" in result.output

    result = runner.invoke(app, ["doctor", "config", "--check"])
    assert result.exit_code == 0, result.output
    assert "clean (0)" in result.output

    result = runner.invoke(app, ["topic", "show", "--topic", "t1"])
    assert result.exit_code == 0, result.output

    result = runner.invoke(app, ["topic", "work"])
    assert result.exit_code == 0, result.output

    result = runner.invoke(app, ["topic", "anomalies"])
    assert result.exit_code == 0, result.output
