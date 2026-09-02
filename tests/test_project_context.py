"""实验 e7244a91（A1~A4）：不可变 ProjectContext 单元测试。

覆盖：
- A4 指纹：``st_dev + st_ino`` 锚点——同 inode 双挂载（符号链接模拟
  /Users/x 与 /Volumes/x 场景）指纹一致不误报；不同 clone 指纹不同。
- A3 config 根：三级优先级（--config-root > MAP_CONFIG_ROOT > workspace）
  与显式根 fail closed（缺目录 / 缺 .map/config.yaml，不回退 CWD）。
- A1 解析：显式 --project-root 永远优先；未传时 CWD 向上查找一次；
  ProjectContext 派生属性（map_dir / content_root / config_root）。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml
from map_client.project_context import (
    ConfigRootNotFoundError,
    ProjectContext,
    ProjectRootNotFoundError,
    fingerprint_warnings,
    resolve_config_root,
    resolve_project_context,
    workspace_fingerprint,
)

# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _make_project(root: Path, *, content_root: str | None = None, key: str = "k1") -> Path:
    """最小可用 workspace：.map/config.yaml（可带自定义 content_root）。"""
    map_dir = root / ".map"
    map_dir.mkdir(parents=True, exist_ok=True)
    config: dict = {"project_key": key, "api_url": "http://localhost:18400"}
    if content_root is not None:
        config["content_root"] = content_root
    (map_dir / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    return root


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    return _make_project(tmp_path / "ws")


# ---------------------------------------------------------------------------
# A4 workspace 指纹
# ---------------------------------------------------------------------------


def test_fingerprint_format_is_dev_ino(workspace: Path) -> None:
    fp = workspace_fingerprint(workspace)
    assert re.fullmatch(r"dev=\d+:ino=\d+", fp), fp
    st = workspace.stat()
    assert fp == f"dev={st.st_dev}:ino={st.st_ino}"


def test_fingerprint_same_inode_dual_mount_not_misflagged(tmp_path: Path) -> None:
    """同 inode 双挂载语义（符号链接模拟）：路径字符串不同、指纹必须一致。

    dogfood 实测场景（/Users/x 与 /Volumes/disk_2/Users/x 同 inode）在
    单机上用「目录 + 指向它的 symlink」复现同一 stat 锚点——路径比对会
    误报，st_dev+st_ino 比对不会。
    """
    real = _make_project(tmp_path / "real-ws")
    alias = tmp_path / "alias-ws"
    alias.symlink_to(real, target_is_directory=True)
    assert alias.resolve() != real.resolve() or str(alias) != str(real)
    assert workspace_fingerprint(alias) == workspace_fingerprint(real)


def test_fingerprint_differs_between_clones(tmp_path: Path) -> None:
    clone_a = _make_project(tmp_path / "clone-a", key="a")
    clone_b = _make_project(tmp_path / "clone-b", key="b")
    assert workspace_fingerprint(clone_a) != workspace_fingerprint(clone_b)


def test_fingerprint_warnings_no_server_path(workspace: Path) -> None:
    assert fingerprint_warnings(workspace_fingerprint(workspace), None) == []
    assert fingerprint_warnings(workspace_fingerprint(workspace), "  ") == []


def test_fingerprint_warnings_same_fingerprint_is_quiet(workspace: Path) -> None:
    fp = workspace_fingerprint(workspace)
    assert fingerprint_warnings(fp, str(workspace)) == []


def test_fingerprint_warnings_unreachable_server_workspace(workspace: Path) -> None:
    warnings = fingerprint_warnings(
        workspace_fingerprint(workspace), str(workspace.parent / "other-machine")
    )
    assert len(warnings) == 1
    assert "not stat-able" in warnings[0]
    assert "rebind" in warnings[0]


def test_fingerprint_warnings_split_brain(tmp_path: Path) -> None:
    local = _make_project(tmp_path / "local", key="a")
    server_ws = _make_project(tmp_path / "server-clone", key="a")
    warnings = fingerprint_warnings(
        workspace_fingerprint(local), str(server_ws)
    )
    assert len(warnings) == 1
    assert "fingerprint mismatch" in warnings[0]
    assert "split-brain" in warnings[0]


# ---------------------------------------------------------------------------
# A3 config 根三级优先级 + fail closed
# ---------------------------------------------------------------------------


def test_resolve_config_root_level3_defaults_to_workspace(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # MAP_CONFIG_ROOT 为空串 = 未设置（不 fail closed 回 workspace）。
    monkeypatch.setenv("MAP_CONFIG_ROOT", "")
    assert resolve_config_root(None, workspace) == workspace


def test_resolve_config_root_level2_env_beats_workspace(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_root = _make_project(tmp_path / "env-root", key="env")
    monkeypatch.setenv("MAP_CONFIG_ROOT", str(config_root))
    assert resolve_config_root(None, workspace) == config_root


def test_resolve_config_root_level1_explicit_beats_env(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_root = _make_project(tmp_path / "env-root", key="env")
    explicit_root = _make_project(tmp_path / "explicit-root", key="explicit")
    monkeypatch.setenv("MAP_CONFIG_ROOT", str(env_root))
    assert resolve_config_root(explicit_root, workspace) == explicit_root


def test_resolve_config_root_fail_closed_missing_dir(
    workspace: Path, tmp_path: Path
) -> None:
    with pytest.raises(ConfigRootNotFoundError) as excinfo:
        resolve_config_root(tmp_path / "nope", workspace)
    assert "No CWD fallback" in str(excinfo.value)


def test_resolve_config_root_fail_closed_missing_config(
    workspace: Path, tmp_path: Path
) -> None:
    bare = tmp_path / "bare"
    bare.mkdir()
    with pytest.raises(ConfigRootNotFoundError) as excinfo:
        resolve_config_root(bare, workspace)
    assert ".map/config.yaml" in str(excinfo.value)
    assert "No CWD fallback" in str(excinfo.value)


def test_resolve_config_root_env_fail_closed(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAP_CONFIG_ROOT", str(tmp_path / "nope"))
    with pytest.raises(ConfigRootNotFoundError):
        resolve_config_root(None, workspace)


# ---------------------------------------------------------------------------
# A1 单点解析
# ---------------------------------------------------------------------------


def test_resolve_context_without_explicit_root_uses_cwd_search_once(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    nested = workspace / "deep" / "inner"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    context = resolve_project_context()
    assert context.workspace_root == workspace
    assert context.map_dir == workspace / ".map"
    assert context.config_root == workspace


def test_resolve_context_explicit_project_root_always_wins(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = _make_project(tmp_path / "other", key="other")
    monkeypatch.chdir(workspace)  # CWD=workspace，显式 root 指向 other
    context = resolve_project_context(project_root=other)
    assert context.workspace_root == other
    assert context.workspace_root != workspace


def test_resolve_context_fail_closed_without_map(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)  # tmp_path 无 .map/（tmp 树上也不会有）
    with pytest.raises(ProjectRootNotFoundError) as excinfo:
        resolve_project_context()
    assert "bootstrap" in str(excinfo.value)


def test_context_content_root_from_config(tmp_path: Path) -> None:
    ws = _make_project(tmp_path / "ws", content_root="custom-root")
    context = resolve_project_context(project_root=ws)
    assert context.content_root == "custom-root"
    assert context.content_dir == ws / "custom-root"


def test_context_content_root_default_is_map(workspace: Path) -> None:
    context = resolve_project_context(project_root=workspace)
    assert context.content_root == "map"
    assert context.content_dir == workspace / "map"


def test_context_is_frozen(workspace: Path) -> None:
    import dataclasses

    context = resolve_project_context(project_root=workspace)
    with pytest.raises(dataclasses.FrozenInstanceError):
        context.persona = "host"  # type: ignore[misc]


def test_context_config_root_follows_explicit_config_root(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_root = _make_project(tmp_path / "cfg", key="cfg")
    monkeypatch.setenv("MAP_CONFIG_ROOT", str(config_root))
    context = resolve_project_context(project_root=workspace)
    # A3 双根：身份来自 config 根，写根仍随 workspace。
    assert context.config_root == config_root
    assert context.config.project_key == "cfg"
    assert context.workspace_root == workspace
    assert context.map_dir == workspace / ".map"


def test_persona_env_leak_isolation(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MAP_CONFIG_ROOT 空串视为未设置：config_root 回 workspace，不误触 fail closed。"""
    monkeypatch.setenv("MAP_CONFIG_ROOT", "")
    context = resolve_project_context(project_root=workspace)
    assert context.config_root == workspace


# ---------------------------------------------------------------------------
# A4：map sync check（fs_status）读路径输出指纹 + warn
# ---------------------------------------------------------------------------


def _seed_topic(workspace: Path, slug: str = "fp-topic") -> None:
    from map_fs import write_topic_index

    (workspace / "map" / "topics").mkdir(parents=True, exist_ok=True)
    write_topic_index(workspace, slug, title="FP Topic", creator="host")


def test_fs_status_surfaces_fingerprint_and_mismatch_warn(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import contextlib
    import uuid as uuid_mod

    from map_client.project_config import ProjectMapConfig
    from map_types.schemas.fs import FsPlaneStatusRead

    import cli.fs_projection as fs_projection
    import cli.main as cli_main
    import cli.project_context as cli_pc
    from cli.commands import fs as fs_cli

    ws = tmp_path / "ws"
    ws.mkdir()
    _seed_topic(ws)
    server_clone = tmp_path / "server-clone"
    server_clone.mkdir()

    fake_context = ProjectContext(
        workspace_root=ws,
        config=ProjectMapConfig(
            map_dir=ws / ".map",
            api_url="http://localhost:18400",
            project_key="fp",
            project_id=None,
            default_persona="host",
            personas={},
            tokens={},
        ),
    )

    class FakeClient:
        def fs_plane_status(self, _pid):
            return FsPlaneStatusRead(
                workspace_path=str(server_clone),
                content_root="map",
                workspace_exists=True,
                content_root_exists=True,
                mode="local-fs",
            )

    @contextlib.contextmanager
    def fake_client_ctx():
        yield FakeClient()

    monkeypatch.setattr(cli_main, "_client_ctx", fake_client_ctx)
    monkeypatch.setattr(cli_pc, "current_context", lambda: fake_context)
    monkeypatch.setattr(fs_cli, "_content_root_name", lambda _ws=None: "map")
    monkeypatch.setattr(
        fs_cli.runner, "_resolve_project", lambda c, p, k: uuid_mod.uuid4()
    )
    monkeypatch.setattr(
        fs_projection,
        "build_diff_payload",
        lambda c, pid, workspace: {
            "local_content_hash": "deadbeef",
            "sync_state": "in-sync",
            "next": None,
        },
    )
    monkeypatch.setattr(
        cli_main,
        "_cli_options",
        {
            **cli_main._cli_options,
            "format": "table",
            "format_source": "explicit --format",
            "project_root": None,
        },
    )

    fs_cli.fs_status()
    captured = capsys.readouterr()
    # 表格视图带指纹行；warn 走 stderr（全格式可见，这里 table 视图验证）。
    assert "fingerprint=dev=" in captured.out
    assert "fingerprint mismatch" in captured.err
    assert "split-brain" in captured.err


def test_fs_status_fingerprint_yaml_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """yaml/json 格式下指纹作为结构化字段输出（脚本可解析）。"""
    import contextlib
    import uuid as uuid_mod

    from map_client.project_config import ProjectMapConfig
    from map_types.schemas.fs import FsPlaneStatusRead

    import cli.fs_projection as fs_projection
    import cli.main as cli_main
    import cli.project_context as cli_pc
    from cli.commands import fs as fs_cli

    ws = tmp_path / "ws-yaml"
    ws.mkdir()
    _seed_topic(ws, "fp-topic-yaml")

    fake_context = ProjectContext(
        workspace_root=ws,
        config=ProjectMapConfig(
            map_dir=ws / ".map",
            api_url="http://localhost:18400",
            project_key="fp",
            project_id=None,
            default_persona="host",
            personas={},
            tokens={},
        ),
    )

    class FakeClient:
        def fs_plane_status(self, _pid):
            return FsPlaneStatusRead(
                workspace_path=str(ws),  # 同 workspace → 无 warn
                content_root="map",
                workspace_exists=True,
                content_root_exists=True,
                mode="local-fs",
            )

    @contextlib.contextmanager
    def fake_client_ctx():
        yield FakeClient()

    monkeypatch.setattr(cli_main, "_client_ctx", fake_client_ctx)
    monkeypatch.setattr(cli_pc, "current_context", lambda: fake_context)
    monkeypatch.setattr(fs_cli, "_content_root_name", lambda _ws=None: "map")
    monkeypatch.setattr(
        fs_cli.runner, "_resolve_project", lambda c, p, k: uuid_mod.uuid4()
    )
    monkeypatch.setattr(
        fs_projection,
        "build_diff_payload",
        lambda c, pid, workspace: {
            "local_content_hash": "deadbeef",
            "sync_state": "in-sync",
            "next": None,
        },
    )
    monkeypatch.setattr(
        cli_main,
        "_cli_options",
        {
            **cli_main._cli_options,
            "format": "yaml",
            "format_source": "explicit --format",
            "project_root": None,
        },
    )

    fs_cli.fs_status()
    captured = capsys.readouterr()
    assert "workspace_fingerprint: dev=" in captured.out
    assert "fingerprint mismatch" not in captured.err
