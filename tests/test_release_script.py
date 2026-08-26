"""Guards for scripts/check-release.sh and scripts/release.sh.

Live-repo consistency stays in test_eng_version_single_source.py. This
module exercises the conductor against a temp tree (MAP_RELEASE_ROOT)
so bump/drift checks do not touch the checkout.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RELEASE_SH = PROJECT_ROOT / "scripts" / "release.sh"
CHECK_RELEASE_SH = PROJECT_ROOT / "scripts" / "check-release.sh"

_MIN_PYPROJECT = """\
[project]
name = "multi-agent-platform"
version = "{version}"
"""

_MIN_SERVER_PKG = """\
[project]
name = "multi-agent-platform-server"
version = "{version}"
dependencies = [
    "multi-agent-platform[server]>={version}",
]
"""

_MIN_DUNDER = '__version__ = "{version}"\n'


def _write_aligned_tree(root: Path, version: str = "0.9.1") -> None:
    (root / "server").mkdir(parents=True)
    (root / "sdk" / "python" / "map_sdk").mkdir(parents=True)
    (root / "server-pkg").mkdir(parents=True)
    (root / "pyproject.toml").write_text(_MIN_PYPROJECT.format(version=version), encoding="utf-8")
    (root / "server" / "__version__.py").write_text(_MIN_DUNDER.format(version=version), encoding="utf-8")
    (root / "sdk" / "python" / "map_sdk" / "__init__.py").write_text(
        _MIN_DUNDER.format(version=version), encoding="utf-8"
    )
    (root / "server-pkg" / "pyproject.toml").write_text(
        _MIN_SERVER_PKG.format(version=version), encoding="utf-8"
    )


def _run(
    script: Path,
    *args: str,
    root: Path | None = None,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if root is not None:
        env["MAP_RELEASE_ROOT"] = str(root)
    return subprocess.run(
        ["bash", str(script), *args],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        check=check,
        env=env,
    )


def test_release_script_is_executable() -> None:
    assert RELEASE_SH.is_file()
    mode = RELEASE_SH.stat().st_mode
    assert mode & stat.S_IXUSR, "scripts/release.sh must be executable"


def test_release_help_lists_stages() -> None:
    result = _run(RELEASE_SH, "help")
    assert result.returncode == 0, result.stderr
    text = result.stdout + result.stderr
    for token in ("bump", "prepare", "tag", "upload"):
        assert token in text
    assert "Does not push remotes" in text or "does not push remotes" in text.lower()


def test_check_release_passes_on_live_repo() -> None:
    result = _run(CHECK_RELEASE_SH)
    assert result.returncode == 0, result.stderr
    assert "consistent" in result.stdout


def test_check_release_passes_on_aligned_fixture(tmp_path: Path) -> None:
    _write_aligned_tree(tmp_path)
    result = _run(CHECK_RELEASE_SH, root=tmp_path)
    assert result.returncode == 0, result.stderr
    assert "0.9.1" in result.stdout


def test_check_release_fails_on_server_pkg_version_drift(tmp_path: Path) -> None:
    _write_aligned_tree(tmp_path)
    pkg = tmp_path / "server-pkg" / "pyproject.toml"
    pkg.write_text(_MIN_SERVER_PKG.format(version="0.8.0"), encoding="utf-8")
    result = _run(CHECK_RELEASE_SH, root=tmp_path)
    assert result.returncode == 1
    assert "drifted" in result.stderr
    assert "server-pkg" in result.stderr


def test_check_release_fails_on_server_pkg_dep_pin_drift(tmp_path: Path) -> None:
    _write_aligned_tree(tmp_path)
    pkg = tmp_path / "server-pkg" / "pyproject.toml"
    pkg.write_text(
        """\
[project]
name = "multi-agent-platform-server"
version = "0.9.1"
dependencies = [
    "multi-agent-platform[server]>=0.8.0",
]
""",
        encoding="utf-8",
    )
    result = _run(CHECK_RELEASE_SH, root=tmp_path)
    assert result.returncode == 1
    assert "0.8.0" in result.stderr


def test_check_release_fails_on_uv_lock_drift(tmp_path: Path) -> None:
    _write_aligned_tree(tmp_path)
    (tmp_path / "uv.lock").write_text(
        'name = "multi-agent-platform"\nversion = "0.8.0"\nsource = { editable = "." }\n',
        encoding="utf-8",
    )
    result = _run(CHECK_RELEASE_SH, root=tmp_path)
    assert result.returncode == 1
    assert "uv.lock" in result.stderr


def test_bump_dry_run_does_not_write(tmp_path: Path) -> None:
    _write_aligned_tree(tmp_path)
    before = (tmp_path / "pyproject.toml").read_text(encoding="utf-8")
    result = _run(RELEASE_SH, "bump", "0.10.0", "--dry-run", root=tmp_path)
    assert result.returncode == 0, result.stderr
    assert "0.9.1 -> 0.10.0" in result.stdout
    assert "dry-run" in result.stdout
    assert (tmp_path / "pyproject.toml").read_text(encoding="utf-8") == before


def test_bump_updates_all_version_sources(tmp_path: Path) -> None:
    _write_aligned_tree(tmp_path)
    result = _run(RELEASE_SH, "bump", "0.10.0", root=tmp_path)
    assert result.returncode == 0, result.stderr + result.stdout
    assert 'version = "0.10.0"' in (tmp_path / "pyproject.toml").read_text(encoding="utf-8")
    assert '__version__ = "0.10.0"' in (tmp_path / "server" / "__version__.py").read_text(
        encoding="utf-8"
    )
    assert '__version__ = "0.10.0"' in (
        tmp_path / "sdk" / "python" / "map_sdk" / "__init__.py"
    ).read_text(encoding="utf-8")
    pkg = (tmp_path / "server-pkg" / "pyproject.toml").read_text(encoding="utf-8")
    assert 'version = "0.10.0"' in pkg
    assert "multi-agent-platform[server]>=0.10.0" in pkg
    check = _run(CHECK_RELEASE_SH, root=tmp_path)
    assert check.returncode == 0, check.stderr


def test_bump_refuses_same_version(tmp_path: Path) -> None:
    _write_aligned_tree(tmp_path)
    result = _run(RELEASE_SH, "bump", "0.9.1", root=tmp_path)
    assert result.returncode == 1
    assert "already at 0.9.1" in result.stderr


def test_bump_refuses_invalid_version(tmp_path: Path) -> None:
    _write_aligned_tree(tmp_path)
    result = _run(RELEASE_SH, "bump", "v0.10.0", root=tmp_path)
    assert result.returncode == 1
    assert "MAJOR.MINOR.PATCH" in result.stderr


def test_prepare_dry_run_lists_gates(tmp_path: Path) -> None:
    _write_aligned_tree(tmp_path)
    result = _run(RELEASE_SH, "prepare", "--dry-run", root=tmp_path)
    assert result.returncode == 0, result.stderr
    assert "sync-web-dist.sh" in result.stdout
    assert "check-packaging.sh" in result.stdout
    assert "dry-run" in result.stdout


def test_tag_dry_run_without_git_fails(tmp_path: Path) -> None:
    _write_aligned_tree(tmp_path)
    result = _run(RELEASE_SH, "tag", "--dry-run", root=tmp_path)
    assert result.returncode == 1
    assert "not a git repository" in result.stderr


def test_upload_without_yes_does_not_require_twine(tmp_path: Path) -> None:
    _write_aligned_tree(tmp_path)
    result = _run(RELEASE_SH, "upload", root=tmp_path)
    assert result.returncode == 0, result.stderr
    assert "dry-run" in result.stdout
    assert "will not git push" in result.stdout
    assert "multi_agent_platform-0.9.1" in result.stdout
    assert "multi_agent_platform_server-0.9.1" in result.stdout
