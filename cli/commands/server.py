"""``map server ...`` sub-app — run the MAP platform without Docker.

Pip users who install ``multi-agent-platform-server`` get the ``map-server`` console
script, but that runs in the *foreground* and blocks the terminal. This sub-app
manages the server as a detached background daemon (PID + log under ``~/.map/``)
and, in ``bootstrap``, wires "ensure the platform is up" together with the existing
project bootstrap so onboarding is one command.

Two concerns are deliberately separate (see the ``server start`` / ``server bootstrap``
split): ``start`` / ``run`` / ``status`` / ``stop`` / ``logs`` only answer
"is the platform running", while ``bootstrap`` additionally connects the current project.
"""
from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

import typer
import yaml

from cli.runner import _print_json  # noqa: E402

server_app = typer.Typer(help="Run / manage the MAP server (no Docker required).")

_DEFAULT_PORT = 18400
_STATE_DIR_NAME = ".map"
_STATE_FILE = "server.yaml"
_PID_FILE = "server.pid"
_LOG_FILE = "server.log"
_HEALTH_TIMEOUT_S = 60


def _state_dir() -> Path:
    from map_client.user_paths import map_state_dir

    return map_state_dir()


def _default_db_url() -> str:
    """User-level SQLite URL (login home, not ``$HOME``)."""
    from map_client.user_paths import default_sqlite_url

    return default_sqlite_url()


def _state_path(port: int) -> Path:
    return _state_dir() / f"{port}.{_STATE_FILE}"


def _log_path(port: int) -> Path:
    return _state_dir() / f"{port}.{_LOG_FILE}"


def _pid_path(port: int) -> Path:
    return _state_dir() / f"{port}.{_PID_FILE}"


def _health_url(port: int) -> str:
    return f"http://127.0.0.1:{port}/health"


def _is_healthy(port: int, timeout: float = 3.0) -> bool:
    try:
        with urllib.request.urlopen(_health_url(port), timeout=timeout) as resp:
            return bool(resp.status == 200)
    except Exception:
        return False


def _read_pid(port: int) -> int | None:
    path = _pid_path(port)
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw.isdigit():
        return None
    return int(raw)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _read_state(port: int) -> dict[str, Any]:
    path = _state_path(port)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_state(port: int, data: dict[str, Any]) -> None:
    _state_dir().mkdir(parents=True, exist_ok=True)
    _state_path(port).write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )


def _find_running_port(preferred: int) -> int | None:
    """Return the port of a running server we manage, or ``None``.

    Prioritises a process recorded for ``preferred``; falls back to scanning the
    state dir so ``status`` is useful even after a port change.
    """
    pid = _read_pid(preferred)
    if pid is not None and _pid_alive(pid) and _is_healthy(preferred):
        return preferred
    for path in _state_dir().glob(f"*.{_STATE_FILE}"):
        try:
            port = int(path.name.split(".")[0])
        except ValueError:
            continue
        if port == preferred:
            continue
        if _read_pid(port) is not None and _is_healthy(port):
            return port
    return None


def _daemon_env(port: int) -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("MAP_PORT", str(port))
    # Pin the resolved URL so a remapped $HOME in the child cannot move the DB.
    env.setdefault("MAP_DATABASE_URL", _default_db_url())
    return env


def _spawn(
    port: int, *, background: bool
) -> subprocess.Popen[Any] | subprocess.CompletedProcess[Any]:
    _state_dir().mkdir(parents=True, exist_ok=True)
    if background:
        # Popen 已把 fd 复制给子进程,退出 with 块关闭父侧句柄即达分离效果。
        with open(_log_path(port), "ab") as log_handle:
            proc = subprocess.Popen(
                [sys.executable, "-m", "cli.server_daemon"],
                env=_daemon_env(port),
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        if proc is not None:
            _pid_path(port).write_text(str(proc.pid), encoding="utf-8")
        _write_state(
            port,
            {
                "pid": proc.pid,
                "port": port,
                "log": str(_log_path(port)),
                "database_url": os.environ.get("MAP_DATABASE_URL") or _default_db_url(),
            },
        )
        return proc
    return subprocess.run(
        [sys.executable, "-m", "cli.server_daemon"],
        env=_daemon_env(port),
        check=False,
    )


def _wait_healthy(port: int, timeout: int = _HEALTH_TIMEOUT_S) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _is_healthy(port):
            return True
        time.sleep(0.5)
    return False


@server_app.command("start")
def server_start(
    port: int = typer.Option(_DEFAULT_PORT, "--port", "-p", help="Port to listen on."),
    foreground: bool = typer.Option(False, "--foreground", help="Run in the foreground (block)."),
) -> None:
    """Start a MAP server for this machine.

    Background by default: spawns a detached daemon under ``~/.map/``, waits for
    ``/health`` and prints the URL. Idempotent — if a managed server is already
    running on ``port`` it is left untouched.

    Notes:
    - start/stop/logs are governed per-port, but the database is shared across all
      ports (``~/.map/data/map.db``); two concurrently-running managed ports use the
      same SQLite store.
    - If ``port`` is already bound by an unmanaged process (e.g. a bare
      ``map server run`` or another app), that process answers ``/health`` and this
      reports success while our own daemon dies on bind — read the log to confirm the
      spawned PID is the healthy one.
    """
    running = _find_running_port(port)
    if not foreground and running is not None:
        typer.echo(f"MAP server already running at http://localhost:{running}")
        typer.echo("Use `map server status` for details, `map server stop` to stop it.")
        return

    if not foreground:
        proc = _spawn(port, background=True)
        assert proc is not None
        if not _wait_healthy(port):
            typer.echo(
                f"Server on port {port} did not become healthy within {_HEALTH_TIMEOUT_S}s. "
                f"See {_log_path(port)}", err=True,
            )
            raise typer.Exit(1)
        typer.echo(f"MAP server started at http://localhost:{port}")
        typer.echo(f"  board : http://localhost:{port}/")
        typer.echo(f"  log   : {_log_path(port)}")
        typer.echo(f"  db    : {_default_db_url()}")
        typer.echo("Connect a project with: map server bootstrap --key <project-key>")
        return

    # Foreground: run to completion (exit code mirrors the server's).
    typer.echo(f"MAP server running in foreground at http://localhost:{port} (Ctrl+C to stop)")
    result = _spawn(port, background=False)
    assert result is not None
    raise typer.Exit(result.returncode or 0)


@server_app.command("run")
def server_run(
    port: int = typer.Option(_DEFAULT_PORT, "--port", "-p", help="Port to listen on."),
) -> None:
    """Alias for a foreground server run (equals the ``map-server`` console script)."""
    result = _spawn(port, background=False)
    assert result is not None
    raise typer.Exit(result.returncode or 0)


@server_app.command("status")
def server_status(
    port: int = typer.Option(_DEFAULT_PORT, "--port", "-p", help="Port to inspect."),
) -> None:
    """Report whether a MAP server is running and where."""

    opts = {"format": "yaml"}
    try:
        from cli.main import _cli_options  # runtime state (monkeypatch surface)
        opts["format"] = _cli_options.get("format", "yaml")
    except Exception:
        pass

    running_port = _find_running_port(port)
    state = _read_state(port)
    payload = {
        "running": running_port is not None,
        "port": running_port or port,
        "healthy": _is_healthy(running_port or port) if running_port else False,
        "url": f"http://localhost:{running_port or port}",
        "pid": _read_pid(running_port or port) if running_port else _read_pid(port),
        "database_url": state.get("database_url") or _default_db_url(),
        "log": str(_log_path(port)),
    }
    if opts["format"] == "json":
        _print_json(payload)
        return

    if not payload["running"]:
        typer.echo("MAP server: NOT running")
        typer.echo(f"Start it with: map server start{'' if port == _DEFAULT_PORT else f' --port {port}'}")
        return
    typer.echo("MAP server: running")
    typer.echo(f"  url  : {payload['url']}")
    typer.echo(f"  pid  : {payload['pid']}")
    typer.echo(f"  db   : {payload['database_url']}")
    typer.echo(f"  log  : {payload['log']}")


@server_app.command("stop")
def server_stop(
    port: int = typer.Option(_DEFAULT_PORT, "--port", "-p", help="Port to stop."),
    force: bool = typer.Option(False, "--force", help="SIGKILL after SIGTERM timeout."),
    timeout: int = typer.Option(10, "--timeout-seconds", help="Seconds to wait after SIGTERM."),
) -> None:
    """Stop a running MAP server (SIGTERM, then SIGKILL if needed)."""
    pid = _read_pid(port)
    if pid is None or not _pid_alive(pid):
        typer.echo(f"No MAP server recorded for port {port}.")
        return
    if not _is_healthy(port) and pid is not None and _pid_alive(pid):
        typer.echo(f"MAP server (pid {pid}) is not responding on /health; stopping anyway.")
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        typer.echo(f"pid {pid} already gone.")
        return
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            typer.echo(f"MAP server (pid {pid}) stopped.")
            _cleanup_state(port)
            return
        time.sleep(0.3)
    if force:
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGKILL)
        typer.echo(f"MAP server (pid {pid}) killed.")
    else:
        typer.echo(f"pid {pid} still alive after {timeout}s; retry with --force.")
        raise typer.Exit(1)
    _cleanup_state(port)


def _cleanup_state(port: int) -> None:
    for path in (_pid_path(port), _state_path(port)):
        with contextlib.suppress(OSError):
            path.unlink()


@server_app.command("logs")
def server_logs(
    port: int = typer.Option(_DEFAULT_PORT, "--port", "-p", help="Port whose log to show."),
    follow: bool = typer.Option(False, "-f", "--follow", help="Tail -f the log."),
    line: int = typer.Option(50, "--line", help="Lines to show when not following."),
) -> None:
    """Show the server log stored under ~/.map/."""
    path = _log_path(port)
    if not path.is_file():
        typer.echo(f"No log found at {path} (start the server first).", err=True)
        raise typer.Exit(1)
    if follow:
        try:
            subprocess.run(["tail", "-f", str(path)], check=False)
        except FileNotFoundError:
            with open(path, encoding="utf-8") as f:
                while True:
                    chunk = f.read()
                    if chunk:
                        sys.stdout.write(chunk)
                        sys.stdout.flush()
                    time.sleep(0.5)
        return
    subprocess.run(["tail", "-n", str(line), str(path)], check=False)


@server_app.command("bootstrap")
def server_bootstrap(
    key: str = typer.Option(..., "--key", help="MAP project_key for this code repo"),
    name: str | None = typer.Option(None, "--name", help="Human-readable MAP project name"),
    path: Path | None = typer.Option(None, "--path", help="workspace_path stored on MAP project"),
    description: str | None = typer.Option(None, "--description"),
    port: int = typer.Option(_DEFAULT_PORT, "--port", "-p", help="MAP API port."),
    project_root: Path | None = typer.Option(None, "--project-root", help="Where to write .map/"),
    force: bool = typer.Option(False, "--force", help="Overwrite existing .map/agents.local.yaml"),
) -> None:
    """Ensure the server is up, then bootstrap the current project into MAP.

    Onboarding entry point for a fresh pip install: starts the server in the
    background when it isn't already running, then delegates the project wiring to the
    same ``bootstrap_project_map`` used by ``map bootstrap``.
    """
    from map_client.bootstrap import bootstrap_project_map
    from map_client.exceptions import MAPHTTPError

    running = _find_running_port(port)
    if running is None:
        _spawn(port, background=True)
        if not _wait_healthy(port):
            typer.echo(
                f"Server on port {port} did not become healthy. See {_log_path(port)}",
                err=True,
            )
            raise typer.Exit(1)
        typer.echo(f"MAP server started at http://localhost:{port}")

    from cli.main import _transport  # runtime state (monkeypatch surface)
    from cli.project_context import default_bootstrap_root

    # 创建型命令：CWD 是「创建 .map/ 的默认目标根」，不是隐式 workspace 解析
    # （守卫唯一落点 cli.project_context.default_bootstrap_root，实验 e7244a91 A5）。
    root = (project_root or default_bootstrap_root()).resolve()
    workspace = path or root
    display_name = name or key
    api_url = f"http://localhost:{port}"
    try:
        result = bootstrap_project_map(
            project_key=key,
            project_name=display_name,
            workspace_path=workspace,
            project_root=root,
            api_url=api_url,
            description=description,
            force=force,
            transport=_transport,
        )
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    except MAPHTTPError as exc:
        typer.echo(f"Error {exc.status_code}: {exc.detail}", err=True)
        raise typer.Exit(1) from exc

    typer.echo(f"Wrote {result.config.map_dir}/")
    typer.echo(f"MAP project_key={result.config.project_key} id={result.config.project_id}")
    typer.echo("Created new MAP project." if result.created_project else "Reused existing MAP project.")
    if result.skipped_agent_names:
        typer.echo(
            "Skipped existing agents (tokens not recoverable via bootstrap): "
            + ", ".join(result.skipped_agent_names),
            err=True,
        )
    typer.echo("Personas: " + ", ".join(result.config.tokens.keys()))
    typer.echo("Try: map --persona host status")
