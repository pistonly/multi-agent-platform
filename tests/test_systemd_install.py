"""Tests for scripts/systemd/map-wakers.service.install.sh"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

INSTALL_SH = Path(__file__).resolve().parents[1] / "scripts/systemd/map-wakers.service.install.sh"


def _fake_systemctl(tmp_path: Path, *, exit_code: int = 0) -> Path:
    script = tmp_path / "systemctl"
    script.write_text(
        f"""#!/usr/bin/env bash
echo "$@" >> "{tmp_path / "systemctl.log"}"
exit {exit_code}
""",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def _run_install(tmp_path: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ.get('PATH', '')}",
        "HOME": str(tmp_path),
    }
    if env:
        merged.update(env)
    return subprocess.run(
        ["bash", str(INSTALL_SH), *args],
        capture_output=True,
        text=True,
        env=merged,
        check=False,
    )


def test_install_registers_unit(tmp_path: Path):
    _fake_systemctl(tmp_path)
    result = _run_install(tmp_path, "--project-root", str(tmp_path))
    assert result.returncode == 0, result.stderr
    log = (tmp_path / "systemctl.log").read_text(encoding="utf-8")
    assert "daemon-reload" in log
    assert "enable --now map-wakers.service" in log
    unit = tmp_path / ".config/systemd/user/map-wakers.service"
    assert unit.is_file()
    assert str(tmp_path) in unit.read_text(encoding="utf-8")


def test_uninstall_disables_unit(tmp_path: Path):
    _fake_systemctl(tmp_path)
    assert _run_install(tmp_path, "--project-root", str(tmp_path)).returncode == 0
    result = _run_install(tmp_path, "--uninstall")
    assert result.returncode == 0, result.stderr
    log = (tmp_path / "systemctl.log").read_text(encoding="utf-8")
    assert "disable --now map-wakers.service" in log


def test_dry_run_does_not_write_unit(tmp_path: Path):
    _fake_systemctl(tmp_path)
    result = _run_install(tmp_path, "--dry-run", "--project-root", str(tmp_path))
    assert result.returncode == 0, result.stderr
    assert "[dry-run]" in result.stdout
    unit = tmp_path / ".config/systemd/user/map-wakers.service"
    assert not unit.exists()


def test_systemd_unavailable_exits_nonzero(tmp_path: Path):
    empty = tmp_path / "empty"
    empty.mkdir()
    result = subprocess.run(
        ["/bin/bash", str(INSTALL_SH), "--dry-run"],
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": str(empty)},
        check=False,
    )
    assert result.returncode == 1
    assert "systemd not available" in result.stderr


def test_permission_denied_on_copy(tmp_path: Path):
    _fake_systemctl(tmp_path)
    read_only = tmp_path / "readonly"
    read_only.mkdir()
    os.chmod(read_only, 0o555)
    result = _run_install(
        tmp_path,
        "--project-root",
        str(tmp_path),
        env={"XDG_CONFIG_HOME": str(read_only)},
    )
    assert result.returncode == 1
    assert "permission denied" in result.stderr.lower()
