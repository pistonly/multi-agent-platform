"""实验 e7244a91（A2/A3）：双根隔离 + config 根 fail closed（subprocess 端到端）。

A2（写根隔离）：CWD=A、``--project-root=B`` 下执行 topic FS 读写命令，
只允许改动 B 下的 ``map/**``（A 下落任何文件即失败）。这是历史事故场景
（身份配置目录 / 代码仓库 / 运行时 CWD 三者不一致导致 server 状态与
``map/**`` 投影漂移）的回归防线。

A3（双根显式化）：``--config-root`` / ``MAP_CONFIG_ROOT`` 显式根缺
``.map/config.yaml`` 时 fail closed（exit != 0 + stderr 指引，不回退
CWD）；三级优先级可观测；``config_root`` 只改身份来源、绝不改写根。

全部用 ``map bootstrap --local``（离线平面，零 server / 零网络）。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _run_cli(
    *args: str,
    cwd: Path,
    env_extra: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-m", "cli.main", *args],
        capture_output=True,
        text=True,
        cwd=str(cwd),
        env=env,
        timeout=120,
    )


def _bootstrap(root: Path, key: str, *, personas: str | None = None) -> None:
    args = ["bootstrap", "--local", "--key", key, "--name", key, "--project-root", str(root)]
    if personas:
        args += ["--personas", personas]
    proc = _run_cli(*args, cwd=REPO_ROOT)
    assert proc.returncode == 0, proc.stderr


def _snapshot(root: Path) -> set[str]:
    return {
        str(p.relative_to(root)) for p in root.rglob("*") if "__pycache__" not in p.parts
    }


def _make_two_projects(tmp_path: Path) -> tuple[Path, Path]:
    ws_a = tmp_path / "ws-a"
    ws_b = tmp_path / "ws-b"
    _bootstrap(ws_a, "iso-a")
    _bootstrap(ws_b, "iso-b")
    return ws_a, ws_b


@pytest.fixture()
def ws_a(tmp_path: Path) -> Path:
    root = tmp_path / "ws-a"
    _bootstrap(root, "iso-a")
    return root


# ---------------------------------------------------------------------------
# A2：写根隔离（CWD=A，--project-root=B）
# ---------------------------------------------------------------------------


def test_dual_root_topic_write_only_touches_project_root(tmp_path: Path) -> None:
    ws_a, ws_b = _make_two_projects(tmp_path)

    # B 下先建一个话题（显式 root 指向 B，落点应为 B）
    proc = _run_cli(
        "--project-root", str(ws_b), "topic", "create", "--title", "B topic",
        cwd=ws_a,
    )
    assert proc.returncode == 0, proc.stderr

    # A 自身也建一个话题（A 是合法 workspace，用来验证「不被污染」）
    proc = _run_cli(
        "--project-root", str(ws_a), "topic", "create", "--title", "A topic",
        cwd=ws_a,
    )
    assert proc.returncode == 0, proc.stderr
    assert (ws_a / "map" / "topics" / "a-topic" / "index.md").is_file()

    before_a = _snapshot(ws_a)

    # CWD=A + --project-root=B：FS 写必须只落 B
    proc = _run_cli(
        "--project-root", str(ws_b), "topic", "comment",
        "--topic", "b-topic", "--body", "hello from cwd-a",
        cwd=ws_a,
    )
    assert proc.returncode == 0, proc.stderr
    assert (ws_b / "map" / "topics" / "b-topic" / "round1-host.md").is_file()

    # A 下落任何文件即失败（A2 判据）
    assert _snapshot(ws_a) == before_a, (
        f"CWD=A --project-root=B leaked writes into A: "
        f"{_snapshot(ws_a) - before_a}"
    )


def test_dual_root_topic_list_reads_project_root_only(tmp_path: Path) -> None:
    ws_a, ws_b = _make_two_projects(tmp_path)
    proc = _run_cli(
        "--project-root", str(ws_b), "topic", "create", "--title", "Only B",
        cwd=ws_a,
    )
    assert proc.returncode == 0, proc.stderr
    proc = _run_cli(
        "--project-root", str(ws_a), "topic", "create", "--title", "Only A",
        cwd=ws_a,
    )
    assert proc.returncode == 0, proc.stderr

    proc = _run_cli("--project-root", str(ws_b), "topic", "list", cwd=ws_a)
    assert proc.returncode == 0, proc.stderr
    assert "Only B" in proc.stdout
    assert "Only A" not in proc.stdout


def test_explicit_project_root_beats_upward_search(tmp_path: Path) -> None:
    """未传 --project-root 时 CWD 向上命中 A；显式给 B 时永远 B（A1）。"""
    ws_a, ws_b = _make_two_projects(tmp_path)
    nested = ws_a / "deep" / "inner"
    nested.mkdir(parents=True)

    proc = _run_cli("--project-root", str(ws_a), "topic", "create", "--title", "In A", cwd=nested)
    assert proc.returncode == 0, proc.stderr
    assert (ws_a / "map" / "topics" / "in-a" / "index.md").is_file()

    proc = _run_cli(
        "--project-root", str(ws_b), "topic", "list", cwd=nested,
    )
    assert proc.returncode == 0, proc.stderr
    assert "In A" not in proc.stdout


# ---------------------------------------------------------------------------
# A3：config 根 fail closed + 三级优先级
# ---------------------------------------------------------------------------


def test_config_root_missing_fails_closed(tmp_path: Path, ws_a: Path) -> None:
    proc = _run_cli(
        "--config-root", str(tmp_path / "nope"), "topic", "list", cwd=ws_a,
    )
    assert proc.returncode != 0
    assert "No CWD fallback" in proc.stderr
    assert ".map/config.yaml" in proc.stderr


def test_config_root_env_missing_fails_closed(
    tmp_path: Path, ws_a: Path
) -> None:
    proc = _run_cli(
        "topic", "list",
        cwd=ws_a,
        env_extra={"MAP_CONFIG_ROOT": str(tmp_path / "nope")},
    )
    assert proc.returncode != 0
    assert "No CWD fallback" in proc.stderr


def test_config_root_env_beats_workspace_identity(
    tmp_path: Path, ws_a: Path
) -> None:
    ws_c = tmp_path / "ws-c"
    _bootstrap(ws_c, "iso-c")
    proc = _run_cli(
        "persona", "list",
        cwd=ws_a,
        env_extra={"MAP_CONFIG_ROOT": str(ws_c)},
    )
    assert proc.returncode == 0, proc.stderr
    # 身份（agents.yaml）来自 config 根 C：agent_name 带 C 的 project_key。
    assert "iso-c-host" in proc.stdout
    assert "iso-a-host" not in proc.stdout


def test_config_root_flag_beats_env(
    tmp_path: Path, ws_a: Path
) -> None:
    ws_c = tmp_path / "ws-c"
    ws_d = tmp_path / "ws-d"
    _bootstrap(ws_c, "iso-c")
    _bootstrap(ws_d, "iso-d")
    proc = _run_cli(
        "--config-root", str(ws_c), "persona", "list",
        cwd=ws_a,
        env_extra={"MAP_CONFIG_ROOT": str(ws_d)},
    )
    assert proc.returncode == 0, proc.stderr
    assert "iso-c-host" in proc.stdout
    assert "iso-d-host" not in proc.stdout


def test_config_root_does_not_change_write_root(
    tmp_path: Path, ws_a: Path
) -> None:
    """A3 核心：config_root 只改身份来源，map/** 写根永远随 workspace_root。"""
    ws_b = tmp_path / "ws-b"
    _bootstrap(ws_b, "iso-b")
    proc = _run_cli(
        "--project-root", str(ws_b), "topic", "create", "--title", "Write root B",
        cwd=ws_a,
    )
    assert proc.returncode == 0, proc.stderr

    before_a = _snapshot(ws_a)
    before_b = _snapshot(ws_b)
    proc = _run_cli(
        "--project-root", str(ws_b), "--config-root", str(ws_a),
        "topic", "comment", "--topic", "write-root-b", "--body", "identity from A",
        cwd=ws_a,
    )
    assert proc.returncode == 0, proc.stderr

    # 写落在 workspace B（config 根 A 只提供身份，绝不被写）
    assert (ws_b / "map" / "topics" / "write-root-b" / "round1-host.md").is_file()
    assert _snapshot(ws_a) == before_a, (
        f"config_root A must never receive writes: {_snapshot(ws_a) - before_a}"
    )
    assert _snapshot(ws_b) - before_b == {
        "map/topics/write-root-b/round1-host.md"
    }
