"""map exp 4e4206de I6：CLI 连接错误输出收敛（A6 验收）。

- 人类可读模式：≤3 行友好错误（无 Rich traceback），固定 exit code 2
- `--json` 模式：单行机器可读信封 `{error, message, hint}` + exit code 2
- `--debug`：保留原始 traceback（异常原样传播）
- 非连接类异常：不被拦截（默认行为不变）
"""

from __future__ import annotations

import json as jsonlib

import httpx
import pytest

import cli.main as cli_main
from cli.errors import WorkerError


def _conn_error() -> httpx.ConnectError:
    return httpx.ConnectError("[Errno 61] Connection refused")


def _wrapped_error() -> WorkerError:
    err = _conn_error()
    wrapped = WorkerError(str(err))
    wrapped.__cause__ = err
    return wrapped


def _reset_cli_options(**overrides) -> None:
    cli_main._cli_options.update(
        {
            "persona": None,
            "project_root": None,
            "config_root": None,
            "format": "yaml",
            "debug": False,
        }
    )
    cli_main._cli_options.update(overrides)


class TestRootConnectionError:
    def test_walks_cause_chain(self):
        _root = cli_main._root_connection_error(_wrapped_error())
        assert isinstance(_root, httpx.ConnectError)

    def test_returns_none_for_unrelated(self):
        assert cli_main._root_connection_error(ValueError("nope")) is None

    def test_handles_direct_error(self):
        assert cli_main._root_connection_error(_conn_error()) is not None


class TestEmitConnectionFailure:
    def test_human_mode_compact(self, capsys):
        _reset_cli_options(format="yaml")
        with pytest.raises(SystemExit) as ei:
            cli_main._emit_connection_failure(_conn_error())
        assert ei.value.code == 2
        err = capsys.readouterr().err
        lines = [ln for ln in err.strip().splitlines() if ln.strip()]
        assert len(lines) <= 3
        assert "Traceback" not in err
        assert "map server start" in err

    def test_json_mode_single_line_envelope(self, capsys):
        _reset_cli_options(format="json")
        with pytest.raises(SystemExit) as ei:
            cli_main._emit_connection_failure(_conn_error())
        assert ei.value.code == 2
        out = capsys.readouterr().out
        lines = [ln for ln in out.strip().splitlines() if ln.strip()]
        assert len(lines) == 1
        payload = jsonlib.loads(lines[0])
        assert set(payload) == {"error", "message", "hint"}
        assert payload["error"] == "connection_error"
        assert "refused" in payload["message"]
        assert "map server start" in payload["hint"]


class TestMainBoundary:
    """走真实 main() 入口的集成路径（monkeypatch _run 注入连接错误）。

    I1 起 work 命令本体在 ``cli.commands.work``（自 main.py 拆出），
    ``_run`` 的注入点是该模块的模块级绑定——patch ``cli.main._run``
    拦截不到拆出后的命令。
    """

    def _invoke_main(self, monkeypatch, argv, exc=None):
        monkeypatch.setattr(
            "sys.argv", ["map", *argv, "work"], raising=True
        )
        thrown = exc if exc is not None else _wrapped_error()
        import cli.commands.work as cli_work

        monkeypatch.setattr(
            cli_work,
            "_run",
            lambda *a, **k: (_ for _ in ()).throw(thrown),
        )
        cli_main.main()

    def test_human_mode_compact(self, monkeypatch, capsys):
        _reset_cli_options()
        with pytest.raises(SystemExit) as ei:
            self._invoke_main(monkeypatch, ["--persona", "host"])
        assert ei.value.code == 2
        err = capsys.readouterr().err
        assert "Traceback" not in err
        assert "map server start" in err

    def test_json_mode_envelope(self, monkeypatch, capsys):
        _reset_cli_options()
        with pytest.raises(SystemExit) as ei:
            self._invoke_main(monkeypatch, ["--persona", "host", "--json"])
        assert ei.value.code == 2
        out = capsys.readouterr().out
        payload = jsonlib.loads(out.strip())
        assert set(payload) == {"error", "message", "hint"}

    def test_debug_mode_keeps_exception(self, monkeypatch, capsys):
        _reset_cli_options()
        with pytest.raises(WorkerError):
            self._invoke_main(monkeypatch, ["--persona", "host", "--debug"])
        captured = capsys.readouterr()
        assert "map server start" not in captured.err

    def test_unrelated_exception_passthrough(self, monkeypatch):
        _reset_cli_options()
        with pytest.raises(ValueError):
            self._invoke_main(monkeypatch, ["--persona", "host"], exc=ValueError("unrelated"))
