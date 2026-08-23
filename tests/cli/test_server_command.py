"""``map server`` 子命令测试。

Unit 层覆盖不真的起服务：状态/探活/PID 均用 monkeypatch，走真实的
``cli.commands.server`` 函数。真实 spawn 子进程（``start`` / ``stop``
生命周期）标 ``integration``，默认被 addopts 排除，CI nightly 才跑。
"""

from __future__ import annotations

import pytest

from cli.commands import server as server_cmd


class _FakeProc:
    def __init__(self, pid: int) -> None:
        self.pid = pid


def test_default_port_and_paths() -> None:
    assert server_cmd._DEFAULT_PORT == 18400
    assert server_cmd._state_dir() == __import__("pathlib").Path.home() / ".map"


def test_db_url_under_home() -> None:
    url = server_cmd._default_db_url()
    assert url.startswith("sqlite:///")
    assert url.endswith("/.map/data/map.db")


def test_read_pid_none_when_missing(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(server_cmd, "_state_dir", lambda: tmp_path)
    assert server_cmd._read_pid(18400) is None


def test_pid_alive(monkeypatch) -> None:
    hit: list[int] = []

    def fake_kill(pid: int, sig: int) -> None:
        hit.append(sig)
        if pid == 999:
            raise ProcessLookupError

    monkeypatch.setattr(server_cmd.os, "kill", fake_kill)
    assert server_cmd._pid_alive(1) is True
    assert server_cmd._pid_alive(999) is False


def test_spawn_background_writes_pid_and_state(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(server_cmd, "_state_dir", lambda: tmp_path)
    monkeypatch.setattr(
        server_cmd.subprocess,
        "Popen",
        lambda *a, **k: _FakeProc(4242),
    )

    proc = server_cmd._spawn(18400, background=True)
    assert proc is not None
    assert proc.pid == 4242
    assert (tmp_path / f"18400.{server_cmd._PID_FILE}").read_text() == "4242"
    state = server_cmd._read_state(18400)
    assert state["port"] == 18400


def test_spawn_foreground_returns_returncode(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(server_cmd, "_state_dir", lambda: tmp_path)
    monkeypatch.setattr(server_cmd.subprocess, "run", lambda *a, **k: type("R", (), {"returncode": 3})())
    r = server_cmd._spawn(18400, background=False)
    assert r is not None and r.returncode == 3


def test_cleanup_state_removes_files(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(server_cmd, "_state_dir", lambda: tmp_path)
    (tmp_path / f"18400.{server_cmd._PID_FILE}").write_text("1")
    server_cmd._cleanup_state(18400)
    assert not (tmp_path / f"18400.{server_cmd._PID_FILE}").exists()
    assert not (tmp_path / f"18400.{server_cmd._STATE_FILE}").exists()


@pytest.mark.integration
def test_start_status_stop_lifecycle(monkeypatch, tmp_path) -> None:
    """真实起一个临时端口的服务，验证 status 可见、重复 start 幂等、stop 干净。"""
    from typer.testing import CliRunner

    from cli.main import app

    # 隔离：让 daemon 与其 PID/日志/DB 全部落在 tmp_path，避免污染真实 ~/.map。
    monkeypatch.setattr(server_cmd, "_state_dir", lambda: tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))

    runner = CliRunner()
    port = 18791
    # 若端口被占则跳过（本机可能已跑其他服务）。
    if server_cmd._find_running_port(port) is not None:
        pytest.skip("test port already in use")

    try:
        r = runner.invoke(app, ["server", "start", "--port", str(port)])
        assert r.exit_code == 0, r.output
        assert "started" in r.output

        r = runner.invoke(app, ["server", "start", "--port", str(port)])
        assert r.exit_code == 0
        assert "already running" in r.output

        r = runner.invoke(app, ["server", "status", "--port", str(port)])
        assert r.exit_code == 0
        assert "running" in r.output
    finally:
        runner.invoke(app, ["server", "stop", "--port", str(port), "--timeout-seconds", "5"])

    r = runner.invoke(app, ["server", "status", "--port", str(port)])
    assert "NOT running" in r.output
