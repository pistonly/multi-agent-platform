"""Unit tests for ``map doctor --config`` (3b7c2b44 A1).

锚定两块判定口径，均以本地 .map/ + 注入替身 client 为准、不碰网络：

- ``inspect_config_divergences`` 各分叉分支（clean / project_id 陈旧 / 缺
  project_id / key 未注册 / agent 字段不符）+ exit code 码表（0/1/2）。
- ``--check`` CLI 层把码表映射到进程退出码，CI 据此红绿；告警钩子仅分叉时出声。
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from map_client.exceptions import MAPHTTPError, MAPNotFoundError
from typer.testing import CliRunner

from cli.commands.doctor import (
    EXIT_CLEAN,
    EXIT_DIAGNOSTIC,
    EXIT_DIVERGED,
    doctor_app,
    inspect_config_divergences,
    warn_config_divergence,
)

AUTHORITY_ID = "11111111-1111-1111-1111-111111111111"


class FakeClient:
    """最小 authority 替身：只实现 doctor 用到的查询方法。"""

    def __init__(self, project=None, agents=(), get_err=None, list_err=None, projects=None):
        self.project = project or SimpleNamespace(id=AUTHORITY_ID)
        self.agents = list(agents)
        self.get_err = get_err
        self.list_err = list_err
        self.projects = list(projects) if projects is not None else []

    def get_project_by_key(self, project_key):  # noqa: ARG002
        if self.get_err is not None:
            raise self.get_err
        return self.project

    def list_agents(self, project_id=None):  # noqa: ARG002
        if self.list_err is not None:
            raise self.list_err
        return self.agents

    def list_projects(self, *, include_archived=False):  # noqa: ARG002
        if self.list_err is not None:
            raise self.list_err
        return self.projects


def _agent(name: str) -> SimpleNamespace:
    return SimpleNamespace(name=name)


def _write_map_dir(
    root: Path,
    *,
    project_id: str | None = AUTHORITY_ID,
    agent_names: tuple[str, ...] = ("multi-agents-platform-host",),
) -> None:
    map_dir = root / ".map"
    map_dir.mkdir(parents=True)
    config = {"project_key": "demo-key", "api_url": "http://localhost:18400"}
    if project_id is not None:
        config["project_id"] = project_id
    (map_dir / "config.yaml").write_text(
        yaml.safe_dump(config), encoding="utf-8"
    )
    personas = {f"p{i}": {"agent_name": name} for i, name in enumerate(agent_names)}
    (map_dir / "agents.yaml").write_text(
        yaml.safe_dump({"personas": personas}), encoding="utf-8"
    )


def test_clean_when_local_matches_authority(tmp_path: Path) -> None:
    _write_map_dir(tmp_path)
    client = FakeClient(agents=[_agent("multi-agents-platform-host")])
    divergences, code = inspect_config_divergences(project_root=tmp_path, client=client)
    assert divergences == []
    assert code == EXIT_CLEAN


def test_stale_project_id_is_config_divergence(tmp_path: Path) -> None:
    _write_map_dir(tmp_path, project_id="22222222-2222-2222-2222-222222222222")
    client = FakeClient(agents=[_agent("multi-agents-platform-host")])
    divergences, code = inspect_config_divergences(project_root=tmp_path, client=client)
    assert code == EXIT_DIVERGED
    assert any(cat == "config" and "project_id 陈旧" in msg for cat, msg in divergences)


def test_missing_project_id_is_config_divergence(tmp_path: Path) -> None:
    _write_map_dir(tmp_path, project_id=None)
    client = FakeClient(agents=[_agent("multi-agents-platform-host")])
    divergences, code = inspect_config_divergences(project_root=tmp_path, client=client)
    assert code == EXIT_DIVERGED
    assert any(cat == "config" and "缺 project_id" in msg for cat, msg in divergences)


def test_unregistered_project_key_is_divergence(tmp_path: Path) -> None:
    _write_map_dir(tmp_path)
    client = FakeClient(get_err=MAPNotFoundError(404, "no such project"))
    divergences, code = inspect_config_divergences(project_root=tmp_path, client=client)
    assert code == EXIT_DIVERGED
    assert any("未在服务端注册" in msg for cat, msg in divergences)


def test_agent_field_mismatch_is_divergence(tmp_path: Path) -> None:
    _write_map_dir(
        tmp_path,
        agent_names=("multi-agents-platform-host", "stray-local-agent"),
    )
    client = FakeClient(
        agents=[
            _agent("multi-agents-platform-host"),
            _agent("multi-agents-platform-participant"),  # 本地缺
        ]
    )
    divergences, code = inspect_config_divergences(project_root=tmp_path, client=client)
    assert code == EXIT_DIVERGED
    agent_divs = [msg for cat, msg in divergences if cat == "agent"]
    assert agent_divs
    assert any("缺权威 agent" in m for m in agent_divs)
    assert any("多出未注册 agent" in m for m in agent_divs)


def test_missing_map_config_is_diagnostic(tmp_path: Path) -> None:
    divergences, code = inspect_config_divergences(project_root=tmp_path, client=FakeClient())
    assert divergences == []
    assert code == EXIT_DIAGNOSTIC


def test_authority_network_error_is_diagnostic(tmp_path: Path) -> None:
    _write_map_dir(tmp_path)
    client = FakeClient(get_err=RuntimeError("connection refused"))
    divergences, code = inspect_config_divergences(project_root=tmp_path, client=client)
    assert divergences == []
    assert code == EXIT_DIAGNOSTIC


def test_authority_http_500_is_diagnostic(tmp_path: Path) -> None:
    _write_map_dir(tmp_path)
    client = FakeClient(get_err=MAPHTTPError(500, "boom"))
    divergences, code = inspect_config_divergences(project_root=tmp_path, client=client)
    assert divergences == []
    assert code == EXIT_DIAGNOSTIC


def test_authority_agent_query_error_is_diagnostic(tmp_path: Path) -> None:
    _write_map_dir(tmp_path)
    client = FakeClient(list_err=MAPHTTPError(403, "nope"))
    divergences, code = inspect_config_divergences(project_root=tmp_path, client=client)
    assert divergences == []
    assert code == EXIT_DIAGNOSTIC


@pytest.mark.parametrize(
    ("code", "marker"),
    [
        (EXIT_CLEAN, "clean"),
        (EXIT_DIVERGED, "diverged"),
        (EXIT_DIAGNOSTIC, "diagnostic-error"),
    ],
)
def test_check_maps_code_table_to_exit_code(
    monkeypatch, tmp_path: Path, code: int, marker: str
) -> None:
    def fake_inspect(*, project_root=None, client=None):  # noqa: ARG001
        items = [("config", "x")] if code == EXIT_DIVERGED else []
        return items, code

    monkeypatch.setattr(
        "cli.commands.doctor.inspect_config_divergences", fake_inspect
    )
    # doctor_app 单命令组在 root 展平：直接传 --check，不写 "config" token
    result = CliRunner().invoke(
        doctor_app, ["--check", "--project-root", str(tmp_path)]
    )
    assert result.exit_code == code
    assert marker in result.output
    assert "(" in result.output  # 码表值写进输出：clean (0) / diverged (1) / diagnostic-error (2)


def test_warn_hook_echoes_on_diverge_only(monkeypatch, capsys, tmp_path: Path) -> None:
    def fake_inspect(*, project_root=None, client=None):  # noqa: ARG001
        return ([("config", "project_id 陈旧")], EXIT_DIVERGED)

    monkeypatch.setattr(
        "cli.commands.doctor.inspect_config_divergences", fake_inspect
    )
    warn_config_divergence(project_root=tmp_path)
    err = capsys.readouterr().err
    assert "WARN" in err
    assert "分叉" in err


def test_warn_hook_silent_on_clean(monkeypatch, capsys, tmp_path: Path) -> None:
    def fake_inspect(*, project_root=None, client=None):  # noqa: ARG001
        return ([], EXIT_CLEAN)

    monkeypatch.setattr(
        "cli.commands.doctor.inspect_config_divergences", fake_inspect
    )
    warn_config_divergence(project_root=tmp_path)
    assert capsys.readouterr().err == ""


# --- 3b7c2b44 A5：workspace_path+content_root 双归属兜底告警 ---


def _proj(key: str, workspace: str, root: str = "map") -> SimpleNamespace:
    return SimpleNamespace(project_key=key, workspace_path=workspace, content_root=root)


def test_workspace_duplicate_is_workspace_divergence(tmp_path: Path) -> None:
    _write_map_dir(tmp_path)
    client = FakeClient(
        agents=[_agent("multi-agents-platform-host")],
        projects=[
            _proj("p-a", "/repo/ws"),
            _proj("p-b", "/repo/ws"),
            _proj("p-c", "/other/ws"),
        ],
    )
    divergences, code = inspect_config_divergences(project_root=tmp_path, client=client)
    assert code == EXIT_DIVERGED
    ws = [msg for cat, msg in divergences if cat == "workspace"]
    assert ws
    assert "/repo/ws" in ws[0] and "p-a" in ws[0] and "p-b" in ws[0]
    assert "p-c" not in ws[0]


def test_workspace_distinct_content_root_not_flagged(tmp_path: Path) -> None:
    _write_map_dir(tmp_path)
    client = FakeClient(
        agents=[_agent("multi-agents-platform-host")],
        projects=[
            _proj("p-a", "/repo/ws", "map"),
            _proj("p-b", "/repo/ws", "docs"),  # 不同 content_root 合法，不报
        ],
    )
    divergences, code = inspect_config_divergences(project_root=tmp_path, client=client)
    assert not any(cat == "workspace" for cat, _ in divergences)


def test_workspace_is_also_clean_if_unique(tmp_path: Path) -> None:
    _write_map_dir(tmp_path)
    client = FakeClient(
        agents=[_agent("multi-agents-platform-host")],
        projects=[_proj("p-only", "/repo/ws")],
    )
    divergences, code = inspect_config_divergences(project_root=tmp_path, client=client)
    assert not any(cat == "workspace" for cat, _ in divergences)
    assert code == EXIT_CLEAN


def test_workspace_list_failure_soft_skip(tmp_path: Path) -> None:
    _write_map_dir(tmp_path)
    client = FakeClient(
        agents=[_agent("multi-agents-platform-host")],
        list_err=MAPHTTPError(403, "nope"),
    )
    divergences, code = inspect_config_divergences(project_root=tmp_path, client=client)
    # list 失败软跳过：不破坏码表语义，agent 查询也走 list_err → 诊断态
    assert not any(cat == "workspace" for cat, _ in divergences)
