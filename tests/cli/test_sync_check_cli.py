"""``map sync check`` CLI 对账门禁：server 侧真实加载契约（实验 M3 A1）。

历史缺陷（bug 审查 H1）：``_load_projection_experiments`` 曾借道
``runner._run``（恒返回 None）+ ``load_config()``（无 project_id 键），
DB 侧恒为空 → 五类对账完全失效（活跃实验恒 divergent、真漂移报干净）。
本文件钉住三条回归线：

1. server 侧数据真实流入五类判定（db_only 活跃 → blocking exit 2）
2. 加载失败显式 exit 1（对账失败 ≠ 对账干净）
3. ``--json`` / ``--exit-code-only`` 输出契约干净（无 action 残留输出）
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from types import SimpleNamespace

import map_client.project_config as pc
import pytest
from typer.testing import CliRunner

import cli.main
from cli.main import app

_PID = uuid.uuid4()


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _fs_experiment_read(**overrides):
    from map_types.schemas.fs import FsExperimentRead

    base = dict(
        id=uuid.uuid4(),
        slug="legacy-active",
        title="legacy active",
        description="",
        phase="running",
        creator="host",
        created_at=None,
        dir_path="map/experiments/legacy-active",
        plan_path=None,
        log_path=None,
        review_path=None,
    )
    base.update(overrides)
    return FsExperimentRead.model_validate(base)


class _StubClient:
    def __init__(
        self,
        experiments: list | None = None,
        *,
        list_error: Exception | None = None,
    ) -> None:
        self.experiments = experiments or []
        self.list_error = list_error
        self.requested_key: str | None = None

    def get_project_by_key(self, key: str):
        return SimpleNamespace(id=_PID)

    def resolve_project_id(self, project_id=None, *, project_key=None):
        if project_id is not None:
            return project_id
        self.requested_key = project_key
        return self.get_project_by_key(project_key or "").id

    def list_fs_experiments(self, project_id):
        assert project_id == _PID
        if self.list_error is not None:
            raise self.list_error
        return list(self.experiments)

    def close(self) -> None:
        pass


def _workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    map_dir = tmp_path / ".map"
    map_dir.mkdir()
    (map_dir / "config.yaml").write_text(
        "api_url: http://localhost:1\n"
        "project_key: synctest\n"
        f"project_id: {_PID}\n"
        "plane: local\n"
        "default_persona: host\n"
        "content_root: map\n",
        encoding="utf-8",
    )
    (map_dir / "agents.yaml").write_text(
        "personas:\n  host:\n    agent_name: synctest-host\n", encoding="utf-8"
    )
    (tmp_path / "map").mkdir()
    monkeypatch.setattr(pc, "find_map_dir", lambda start=None: map_dir)
    monkeypatch.setattr(cli.main, "find_map_dir", lambda start=None: map_dir)
    return tmp_path


def _install(monkeypatch: pytest.MonkeyPatch, client: _StubClient) -> None:
    monkeypatch.setattr(cli.main, "resolve_client", lambda **kwargs: client)


def test_db_side_flows_into_classification(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """server 侧 active 实验且 FS 缺失 → db_only blocking（exit 2）。

    历史缺陷下 DB 侧恒空，此场景会假报 total=0 干净 exit 0。
    """
    _workspace(tmp_path, monkeypatch)
    _install(
        monkeypatch,
        _StubClient([_fs_experiment_read(phase="running")]),
    )
    result = runner.invoke(app, ["sync", "check"])
    assert result.exit_code == 2, result.output
    assert "db_only=1" in result.output
    assert "legacy-active" in result.output


def test_db_terminal_only_not_blocking(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _workspace(tmp_path, monkeypatch)
    _install(
        monkeypatch,
        _StubClient([_fs_experiment_read(slug="old", phase="done")]),
    )
    result = runner.invoke(app, ["sync", "check"])
    assert result.exit_code == 0, result.output
    assert "db_only=1" in result.output


def test_server_load_failure_exit_1(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """加载失败 exit 1 + 指引文案；绝不输出干净结论。"""
    _workspace(tmp_path, monkeypatch)
    _install(monkeypatch, _StubClient(list_error=RuntimeError("server unreachable")))
    result = runner.invoke(app, ["sync", "check"])
    assert result.exit_code == 1, result.output
    assert "加载失败" in result.output
    assert "blocking=0" not in result.output


def test_project_key_option_reaches_resolution(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--project-key 不再被静默忽略，真实参与 project 解析。"""
    _workspace(tmp_path, monkeypatch)
    client = _StubClient()
    _install(monkeypatch, client)
    result = runner.invoke(app, ["sync", "check", "--project-key", "explicit-key"])
    assert result.exit_code == 0, result.output
    assert client.requested_key == "explicit-key"


def test_json_output_is_pure_json(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--json 输出可整体 json.loads（历史上会先混入一行 action 残留 []）。"""
    _workspace(tmp_path, monkeypatch)
    _install(monkeypatch, _StubClient())
    result = runner.invoke(app, ["sync", "check", "--json"])
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert parsed["total"] == 0


def test_exit_code_only_silent(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--exit-code-only 零输出（承诺 suppress output）。"""
    _workspace(tmp_path, monkeypatch)
    _install(monkeypatch, _StubClient())
    result = runner.invoke(app, ["sync", "check", "--exit-code-only"])
    assert result.exit_code == 0, result.output
    assert result.output == ""
