"""``map work`` 默认精简视图测试（map exp 4e4206de I1 / A1）。

主判据（A1）：固定状态夹具（空 todos 分区 + 空通知 + 固定 agent 块 +
固定 persona 心跳数）渲染输出 ≤800B。before/after 实测字节数对比只是
evidence，不在此断言（真实快照含动态数据）。

契约面（A1 硬约束）：``--format json`` 输出与改前逐字段一致——以
``data == fixture.model_dump(mode="json")`` 固定；``--verbose`` 等价于
显式 ``--format yaml`` 的完整诊断视图。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

from map_types.schemas.agent_work import AgentWorkRead
from map_types.schemas.notification import NotificationListRead
from map_types.schemas.todo import TodoRead
from map_types.schemas.topic_progress import TopicProgressListRead
from typer.testing import CliRunner

import cli.main as cli_main
from cli.main import app
from cli.work_compact_view import render_work_compact

_FIX_AGENT = {
    "id": "b047ec7d-dc7b-483c-9c76-f88cd9000a02",
    "name": "multi-agent-platform-host",
    "role": "agent",
    "project_id": "7490e7d7-320a-46b6-bc8d-582cd2694529",
    "project_key": "multi-agent-platform",
    "created_at": "2026-09-02T02:07:57",
}


def _fixture_work() -> AgentWorkRead:
    return AgentWorkRead(
        agent=_FIX_AGENT,
        topic_progress=TopicProgressListRead(items=[], total=0),
        todos=TodoRead(),
        notifications=NotificationListRead(items=[], total=0, unread_count=0),
        source=None,
    )


class _FixedHeartbeats:
    """固定 persona 心跳数（banner 走 stderr，不进 stdout 尺寸判据）。"""

    def get_agent_work(self, **_kwargs):
        return self._work

    def get_global_status(self):
        ts = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
        return SimpleNamespace(
            waker_heartbeats=[
                SimpleNamespace(
                    agent_name="multi-agent-platform-host",
                    persona="host",
                    stale=False,
                    last_waker_poll_at=ts,
                    last_busy_since=None,
                ),
                SimpleNamespace(
                    agent_name="multi-agent-platform-participant",
                    persona="participant",
                    stale=False,
                    last_waker_poll_at=ts,
                    last_busy_since=None,
                ),
            ]
        )


def _patch_client(monkeypatch, work) -> None:
    fake = _FixedHeartbeats()
    fake._work = work

    class _CM:
        def __enter__(self_inner):
            return fake

        def __exit__(self_inner, *args):
            return False

    monkeypatch.setattr(cli_main, "_client_ctx", lambda: _CM())
    monkeypatch.setattr(cli_main, "_transport", None)


def test_compact_fixture_under_800_bytes(monkeypatch) -> None:
    _patch_client(monkeypatch, _fixture_work())
    result = CliRunner().invoke(app, ["work"])
    assert result.exit_code == 0, result.output
    assert len(result.stdout.encode("utf-8")) <= 800


def test_compact_fixture_snapshot_lines(monkeypatch) -> None:
    _patch_client(monkeypatch, _fixture_work())
    result = CliRunner().invoke(app, ["work"])
    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert lines[0] == (
        "agent: multi-agent-platform-host (agent) project=multi-agent-platform id=b047ec7d"
    )
    assert "topic_progress: 0 topics" in lines
    assert "notifications: 0 unread" in lines
    empty_line = next(ln for ln in lines if ln.startswith("  (empty:"))
    for section in ("my_open_experiments", "mentions", "action_items"):
        assert section in empty_line
    # 心跳 banner（固定 2 persona）在 stderr，stdout 保持精简
    assert "waker" not in result.stdout


def test_json_contract_unchanged_field_by_field(monkeypatch) -> None:
    fixture = _fixture_work()
    _patch_client(monkeypatch, fixture)
    result = CliRunner().invoke(app, ["work", "--format", "json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["data"] == fixture.model_dump(mode="json")


def test_verbose_equals_explicit_yaml_full_view(monkeypatch) -> None:
    _patch_client(monkeypatch, _fixture_work())
    runner = CliRunner()
    verbose = runner.invoke(app, ["work", "--verbose"])
    explicit = runner.invoke(app, ["work", "--format", "yaml"])
    assert verbose.exit_code == 0, verbose.output
    assert explicit.exit_code == 0, explicit.output
    # 完整诊断视图：agent 块为多行 YAML（含完整 id），与显式 yaml 逐字节一致
    assert verbose.stdout == explicit.stdout
    assert "id: b047ec7d-dc7b-483c-9c76-f88cd9000a02" in verbose.stdout


def test_default_view_omits_diagnostic_fields(monkeypatch) -> None:
    work = _fixture_work()
    work.notifications = NotificationListRead(
        items=[
            {
                "id": "97c95cfa-ff34-4df6-ac3a-88075f46f314",
                "recipient_agent_id": "b047ec7d-dc7b-483c-9c76-f88cd9000a02",
                "project_id": "7490e7d7-320a-46b6-bc8d-582cd2694529",
                "event": "review.submitted",
                "summary": "提交评审",
                "target_type": "review",
                "target_id": "de15c7d4-a32a-4c7c-b534-8e371f5a038b",
                "category": "digest",
                "group_key": "recipient:x:project:y:review:z:review.submitted",
                "payload_json": {"experiment_id": "4e4206de-eba1-4de2-b5a6-7ff1d8adfda2"},
                "read_at": None,
                "created_at": "2026-09-20T10:28:42",
                "updated_at": "2026-09-20T10:28:42",
            }
        ],
        total=1,
        unread_count=1,
    )
    _patch_client(monkeypatch, work)
    result = CliRunner().invoke(app, ["work"])
    assert result.exit_code == 0, result.output
    for diagnostic in ("group_key", "recipient_agent_id", "wake_version", "fingerprint_version"):
        assert diagnostic not in result.stdout
    assert "  - [digest] review.submitted — 提交评审 -> review de15c7d4" in result.stdout


def test_dict_payload_supported(monkeypatch) -> None:
    """非 pydantic 载荷（mock/直连路径）同样可渲染，不抛 AttributeError。"""
    payload = {
        "agent": dict(_FIX_AGENT),
        "topic_progress": {"items": [], "total": 0},
        "todos": {},
        "notifications": {"items": [], "total": 0, "unread_count": 0},
    }
    _patch_client(monkeypatch, payload)
    result = CliRunner().invoke(app, ["work"])
    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines()[0].startswith("agent: multi-agent-platform-host")


def test_render_work_compact_nonempty_experiment() -> None:
    work = {
        "agent": dict(_FIX_AGENT),
        "topic_progress": {"items": [], "total": 0},
        "todos": {
            "my_open_experiments": [
                {
                    "id": "4e4206de-eba1-4de2-b5a6-7ff1d8adfda2",
                    "title": "token 开销阶段一",
                    "phase": "running",
                    "plan_file_path": "map/experiments/token-quickwins-and-measurement/plan.md",
                    "actions": ["complete"],
                    "blocked_on": "none",
                }
            ]
        },
        "notifications": {"items": [], "total": 0, "unread_count": 0},
    }
    out = render_work_compact(work)
    assert "my_open_experiments: 1" in out
    assert "4e4206de [running] token 开销阶段一" in out
    assert "plan: map/experiments/token-quickwins-and-measurement/plan.md" in out
    assert "actions: complete" in out
    assert "blocked_on" not in out  # none 不渲染
