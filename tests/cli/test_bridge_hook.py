"""``map bridge hook`` 命令测试（实验 db97aeac I2 / A1+A4 投递部分）。

monkeypatch ``cli.commands.bridge._fetch_work`` 与 ``current_context``
隔离 API 与 workspace；验证 Stop hook 契约：首次 block JSON、冷却沉默、
重复非阻塞升级、上限沉默、无待办仅心跳、fetch 失败静默 exit 0。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

import cli.commands.bridge as bridge
from cli.interactive_bridge import bridge_state_path, load_state
from cli.main import app

NOW = datetime(2026, 9, 4, 8, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def bridge_env(tmp_path: Path, monkeypatch):
    """固定 workspace/persona；返回可调 work payload 的工厂。"""
    context = SimpleNamespace(
        workspace_root=tmp_path, config=SimpleNamespace(default_persona="host")
    )
    monkeypatch.setattr(bridge, "current_context", lambda: context)
    state = {"work": None}

    def set_work(work):
        state["work"] = work

    def fake_fetch():
        if state["work"] is None:
            raise RuntimeError("no work stubbed")
        return state["work"]

    monkeypatch.setattr(bridge, "_fetch_work", fake_fetch)
    return SimpleNamespace(set_work=set_work, root=tmp_path)


def _work_with_obligation() -> dict:
    return {
        "topic_progress": {
            "items": [
                {
                    "topic_title": "某话题",
                    "discussion_round": "round1",
                    "work_items": [
                        {
                            "kind": "stale_open_topics",
                            "priority": "obligation",
                            "idempotency_key": "fs:stale:x",
                        }
                    ],
                }
            ]
        },
        "todos": {"mentions": [{}]},
        "notifications": {"items": [], "unread_count": 0},
    }


def test_first_obligation_blocks_with_summary(runner, bridge_env):
    bridge_env.set_work(_work_with_obligation())
    result = runner.invoke(app, ["bridge", "hook"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["decision"] == "block"
    assert "某话题" in payload["reason"]
    assert "stale_open_topics" in payload["reason"]
    assert "mentions：1 项" in payload["reason"]
    assert "wake.md" in payload["reason"]

    state = load_state(bridge_state_path(bridge_env.root, "host"))
    assert state["remind_count"] == 1
    assert state["runtime"] == "claude-code"
    assert "last_seen_at" in state and "fingerprint" in state


def test_repeat_within_cooldown_silent(runner, bridge_env):
    bridge_env.set_work(_work_with_obligation())
    assert json.loads(runner.invoke(app, ["bridge", "hook"]).stdout)["decision"] == "block"
    again = runner.invoke(app, ["bridge", "hook"])
    assert again.exit_code == 0
    assert again.stdout.strip() == ""


def test_repeat_after_cooldown_non_blocking_escalation(runner, bridge_env, monkeypatch):
    bridge_env.set_work(_work_with_obligation())
    runner.invoke(app, ["bridge", "hook"])
    # 把 last_reminded_at 拨回冷却期外
    path = bridge_state_path(bridge_env.root, "host")
    state = load_state(path)
    state["last_reminded_at"] = (NOW - timedelta(hours=1)).isoformat()
    bridge.save_state(path, state)

    class _PastNow:
        @staticmethod
        def now(tz=None):
            return NOW

    monkeypatch.setattr(bridge, "datetime", _PastNow)
    result = runner.invoke(app, ["bridge", "hook"])
    assert result.exit_code == 0
    assert result.stdout.strip() == ""  # 非阻塞：不写 stdout
    assert "第 2 次提醒" in (result.stderr or "")
    assert load_state(path)["remind_count"] == 2


def test_silenced_after_max_count(runner, bridge_env):
    bridge_env.set_work(_work_with_obligation())
    path = bridge_state_path(bridge_env.root, "host")
    runner.invoke(app, ["bridge", "hook"])
    state = load_state(path)
    state["remind_count"] = 3
    state["last_reminded_at"] = "2020-01-01T00:00:00+00:00"
    bridge.save_state(path, state)
    result = runner.invoke(app, ["bridge", "hook"])
    assert result.exit_code == 0
    assert result.stdout.strip() == ""
    assert (result.stderr or "").strip() == ""


def test_no_work_writes_heartbeat_only(runner, bridge_env):
    bridge_env.set_work(
        {"topic_progress": {"items": []}, "todos": {}, "notifications": {"items": [], "unread_count": 0}}
    )
    result = runner.invoke(app, ["bridge", "hook"])
    assert result.exit_code == 0
    assert result.stdout.strip() == ""
    state = load_state(bridge_state_path(bridge_env.root, "host"))
    assert "last_seen_at" in state
    assert "fingerprint" not in state


def test_fetch_failure_silent_exit_zero(runner, bridge_env):
    # 不 set_work：_fetch_work 抛错，hook 必须静默 exit 0
    result = runner.invoke(app, ["bridge", "hook"])
    assert result.exit_code == 0
    assert result.stdout.strip() == ""


def test_fetch_failure_debug_visible_with_env(runner, bridge_env, monkeypatch):
    monkeypatch.setenv("MAP_BRIDGE_DEBUG", "1")
    result = runner.invoke(app, ["bridge", "hook"])
    assert result.exit_code == 0
    assert "[bridge:debug]" in (result.stderr or "")
