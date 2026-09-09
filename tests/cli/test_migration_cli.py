"""``map sync migrate ...`` CLI：HTTP-only 契约（实验 M3 A2）。

历史缺陷（bug 审查 M6）：CLI 直接 ``from server.db.session import
SessionLocal``——绕过 API 权限/审计，且在非 server 主机上静默新建空库
（scan 报 scanned=0 假成功）。本文件钉住：

1. 五个子命令全部经 SDK client（HTTP），不再触 DB；
2. ``dry-run`` 只调 plan（零写副作用，host worker --dry-run 会真实执行）；
3. ``execute --apply/--limit`` 语义透传；dry 缺省 apply=False；
4. ``verify`` 退出码契约（0 全 verified / 2 有 mismatch），LKG 锚点
   只在对账健康时写、且按 project_key 作用域命名 + 原子写。
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from types import SimpleNamespace

import map_client.project_config as pc
import pytest
from typer.testing import CliRunner

import cli.main
from cli.main import app

_PID = uuid.uuid4()


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _verify_report(**overrides):
    base = {
        "verified_count": 1,
        "mismatch_count": 0,
        "missing_count": 0,
        "verified": [{"kind": "experiment", "slug": "e1", "fields": []}],
        "mismatched": [],
        "missing": [],
        "summary": {"applied": 1, "pending": 0, "failed": 0, "skipped": 0},
        "generated_at": "2026-09-08T00:00:00+00:00",
    }
    base.update(overrides)
    return base


class _StubClient:
    """五端点 stub；write 端点被意外调用即 raise（钉 dry-run 只读契约）。"""

    def __init__(
        self,
        *,
        plan: dict | None = None,
        execute_report: dict | None = None,
        verify: dict | None = None,
        status: dict | None = None,
    ) -> None:
        self.plan = plan or {
            "actionable_count": 1,
            "stale_in_flight": 0,
            "summary": {"pending": 1},
            "actionable": [
                {
                    "id": "abc123",
                    "kind": "experiment",
                    "slug": str(_PID),
                    "content_hash": "0" * 64,
                    "attempts": 0,
                    "status": "pending",
                }
            ],
        }
        self.execute_report = execute_report or {
            "run_id": "r1",
            "apply": False,
            "stale_in_flight_reset": 0,
            "processed": 1,
            "failed": 0,
            "unclaimed": 0,
            "summary": {"skipped": 1},
        }
        self.verify = verify if verify is not None else _verify_report()
        self.status = status or {
            "summary": {"applied": 1},
            "latest_run": {
                "run_id": "r1",
                "phase": "execute",
                "started_at": "2026-09-08T00:00:00+00:00",
                "finished_at": None,
                "summary": None,
            },
        }
        self.calls: list[tuple[str, dict]] = []
        self.requested_key: str | None = None

    def get_project_by_key(self, key: str):
        return SimpleNamespace(id=_PID)

    def resolve_project_id(self, project_id=None, *, project_key=None):
        if project_id is not None:
            return project_id
        self.requested_key = project_key
        return self.get_project_by_key(project_key or "").id

    def fs_migration_scan(self, project_id):
        self.calls.append(("scan", {"project_id": project_id}))
        return {"run_id": "r0", "scanned": 1, "inserted": 1, "skipped_existing": 0,
                "by_kind": {"experiment": 1}, "by_status": {"pending": 1}}

    def fs_migration_plan(self, project_id, limit=50):
        self.calls.append(("plan", {"project_id": project_id, "limit": limit}))
        return self.plan

    def fs_migration_execute(self, project_id, *, apply, limit=50):
        self.calls.append(("execute", {"project_id": project_id, "apply": apply, "limit": limit}))
        return self.execute_report

    def fs_migration_status(self, project_id):
        self.calls.append(("status", {"project_id": project_id}))
        return self.status

    def fs_migration_verify(self, project_id):
        self.calls.append(("verify", {"project_id": project_id}))
        return self.verify

    def close(self) -> None:
        pass


def _workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    map_dir = tmp_path / ".map"
    map_dir.mkdir()
    (map_dir / "config.yaml").write_text(
        "api_url: http://localhost:1\n"
        "project_key: migtest\n"
        f"project_id: {_PID}\n"
        "plane: local\n"
        "default_persona: host\n"
        "content_root: map\n",
        encoding="utf-8",
    )
    (map_dir / "agents.yaml").write_text(
        "personas:\n  host:\n    agent_name: migtest-host\n", encoding="utf-8"
    )
    (tmp_path / "map").mkdir()
    monkeypatch.setattr(pc, "find_map_dir", lambda start=None: map_dir)
    monkeypatch.setattr(cli.main, "find_map_dir", lambda start=None: map_dir)
    return tmp_path


def _install(monkeypatch: pytest.MonkeyPatch, client: _StubClient) -> None:
    monkeypatch.setattr(cli.main, "resolve_client", lambda **kwargs: client)


def test_dry_run_is_read_only_plan(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """dry-run 只调 plan；scan/execute/verify/status 一概不触。"""
    _workspace(tmp_path, monkeypatch)
    client = _StubClient()
    _install(monkeypatch, client)
    result = runner.invoke(app, ["sync", "migrate", "dry-run"])
    assert result.exit_code == 0, result.output
    assert [name for name, _ in client.calls] == ["plan"]
    assert "actionable=1" in result.output
    assert str(_PID) in result.output


def test_execute_semantics_passthrough(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """缺省 dry（apply=False）；--apply/--limit 透传给 server。"""
    _workspace(tmp_path, monkeypatch)
    client = _StubClient()
    _install(monkeypatch, client)
    result = runner.invoke(app, ["sync", "migrate", "execute"])
    assert result.exit_code == 0, result.output
    assert client.calls[0] == (
        "execute",
        {"project_id": _PID, "apply": False, "limit": 50},
    )

    result = runner.invoke(
        app, ["sync", "migrate", "execute", "--apply", "--limit", "7"]
    )
    assert result.exit_code == 0, result.output
    assert client.calls[1] == (
        "execute",
        {"project_id": _PID, "apply": True, "limit": 7},
    )


def test_verify_clean_writes_project_scoped_lkg(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """verify 全绿 → exit 0 + LKG 锚点写 <content_root>/.fs-migration/<key>-lkg.json。"""
    ws = _workspace(tmp_path, monkeypatch)
    client = _StubClient()
    _install(monkeypatch, client)
    result = runner.invoke(app, ["sync", "migrate", "verify"])
    assert result.exit_code == 0, result.output
    lkg = ws / "map" / ".fs-migration" / "migtest-lkg.json"
    assert lkg.is_file()
    payload = json.loads(lkg.read_text(encoding="utf-8"))
    assert payload["project_id"] == str(_PID)
    assert payload["schema"] == "fs-migration.last-known-good/v2"
    # 无半截文件残留（原子写）
    assert not (ws / "map" / ".fs-migration" / "migtest-lkg.json.tmp").exists()


def test_verify_mismatch_exit_2_no_lkg(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """verify 有 mismatch → exit 2 + 明细；绝不写 LKG（对账不健康不留锚）。"""
    ws = _workspace(tmp_path, monkeypatch)
    client = _StubClient(
        verify=_verify_report(
            verified_count=0,
            mismatch_count=1,
            verified=[],
            mismatched=[
                {
                    "kind": "experiment",
                    "slug": "e1",
                    "fields": [
                        {"field": "title", "db": "a", "fs_view": "b"}
                    ],
                }
            ],
        )
    )
    _install(monkeypatch, client)
    result = runner.invoke(app, ["sync", "migrate", "verify"])
    assert result.exit_code == 2, result.output
    assert "MISMATCH" in result.output
    assert "title" in result.output
    assert not (ws / "map" / ".fs-migration" / "migtest-lkg.json").exists()


def test_status_without_run_hint(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """project 级 status：有 run 显示 run；无 run 给 scan 指引（不再是假 0）。"""
    _workspace(tmp_path, monkeypatch)
    client = _StubClient(status={"summary": {"pending": 1}, "latest_run": None})
    _install(monkeypatch, client)
    result = runner.invoke(app, ["sync", "migrate", "status"])
    assert result.exit_code == 0, result.output
    assert "pending" in result.output
    assert "scan" in result.output


def test_json_output_parseable(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--json 输出可整体 json.loads（机器可读契约）。"""
    _workspace(tmp_path, monkeypatch)
    client = _StubClient()
    _install(monkeypatch, client)
    for sub in ("dry-run", "execute", "verify", "status", "scan"):
        result = runner.invoke(app, ["sync", "migrate", sub, "--json"])
        assert result.exit_code == 0, f"{sub}: {result.output}"
        parsed = json.loads(result.output)
        assert isinstance(parsed, dict)
