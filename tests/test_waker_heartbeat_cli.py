"""CLI side of waker-heartbeat visibility (experiment 1b605e0b).

Locks two contracts, both through the real typer app with a stubbed transport:
- ``map work --client waker`` forwards ``client="waker"`` to the server (so the
  server refreshes ``last_waker_poll_at``);
- ``map work`` renders the server-computed ``waker_heartbeats[]`` banner to
  stderr (stdout stays a pure YAML work snapshot): stale rows warn, never rows are
  quiet state lines.

The banner is pure rendering — the ``stale`` flag arrives baked from the server and is
never re-derived in the CLI.
"""

import datetime as _dt
from types import SimpleNamespace

from typer.testing import CliRunner

from cli import main as cli_main
from cli.main import app


def runner() -> CliRunner:
    return CliRunner()


def _activity_payload() -> dict:
    return {
        "agent": {"id": "a1", "name": "host-agent", "persona": "host"},
        "todos": {"items": [], "total": 0},
        "notifications": {"items": [], "total": 0, "unread_count": 0},
    }


def _hb_row(agent_name, persona, last_waker_poll_at, stale):
    return SimpleNamespace(
        agent_id="a1",
        agent_name=agent_name,
        persona=persona,
        last_waker_poll_at=last_waker_poll_at,
        stale=stale,
    )


def _fake_ctx(client_obj):
    class _CM:
        def __enter__(self_inner):
            return client_obj

        def __exit__(self_inner, *args):
            return False

    return _CM()


def test_work_passthrough_client_waker(monkeypatch):
    captured = {}

    class _FakeClient:
        def get_agent_work(self, **kwargs):
            captured.update(kwargs)
            return _activity_payload()

        def get_global_status(self):
            return SimpleNamespace(waker_heartbeats=[])

    monkeypatch.setattr(cli_main, "_client_ctx", lambda: _fake_ctx(_FakeClient()))
    result = runner().invoke(app, ["work", "--client", "waker"])
    assert result.exit_code == 0, result.stderr
    assert captured.get("client") == "waker"


def test_work_passthrough_no_client(monkeypatch):
    captured = {}

    class _FakeClient:
        def get_agent_work(self, **kwargs):
            captured.update(kwargs)
            return _activity_payload()

        def get_global_status(self):
            return SimpleNamespace(waker_heartbeats=[])

    monkeypatch.setattr(cli_main, "_client_ctx", lambda: _fake_ctx(_FakeClient()))
    result = runner().invoke(app, ["work"])
    assert result.exit_code == 0, result.stderr
    assert captured.get("client") is None


def test_work_renders_stale_warn(monkeypatch):
    now = _dt.datetime.now(_dt.timezone.utc)
    old = now - _dt.timedelta(minutes=30)

    class _FakeClient:
        def get_agent_work(self, **kwargs):
            return _activity_payload()

        def get_global_status(self):
            return SimpleNamespace(
                waker_heartbeats=[
                    _hb_row("host-agent", "host", old, True),
                    _hb_row("participant-agent", "participant", now, False),
                ]
            )

    monkeypatch.setattr(cli_main, "_client_ctx", lambda: _fake_ctx(_FakeClient()))
    result = runner().invoke(app, ["work"])
    assert result.exit_code == 0, result.stderr
    assert "[WARN] waker heartbeat stale for host-agent(host)" in (result.stderr or "")
    # fresh participant row shows a state line but never warns
    assert "[WARN] waker heartbeat stale for participant-agent" not in (result.stderr or "")


def test_work_never_persona_quiet(monkeypatch):
    class _FakeClient:
        def get_agent_work(self, **kwargs):
            return _activity_payload()

        def get_global_status(self):
            return SimpleNamespace(
                waker_heartbeats=[
                    _hb_row("host-agent", "host", None, False),  # never heartbeated
                ]
            )

    monkeypatch.setattr(cli_main, "_client_ctx", lambda: _fake_ctx(_FakeClient()))
    result = runner().invoke(app, ["work"])
    assert result.exit_code == 0, result.stderr
    assert "host-agent" in (result.stderr or "")  # state line still shown
    assert "WARN" not in (result.stderr or "")  # never -> no warning
