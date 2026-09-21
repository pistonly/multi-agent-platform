"""``map experiment show``/``status``/``complete``/``log`` 默认精简视图测试
（map exp 4e4206de I4 / A4）。

契约面（A4 硬约束）：
* ``--format json`` 输出与改前逐字段一致 — ``data == fixture.model_dump(mode="json")``；
* ``--full`` 与显式 ``--format yaml`` 恢复改前全文（逐字节一致）；
* 默认视图不内联 plan ``content_md`` 全文（指针 + 摘录 + 行数标注）。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
import yaml
from map_types.schemas import ExperimentDetailRead, LogCreateResponse
from typer.testing import CliRunner

import cli.main as cli_main
import cli.commands.experiment as experiment_module
from cli.main import app

EXP_ID = "3f2a1c9e-5b7d-4e8f-9a0b-1c2d3e4f5a6b"
PROJECT_ID = "7490e7d7-320a-46b6-bc8d-582cd2694529"
CREATOR_ID = "b047ec7d-dc7b-483c-9c76-f88cd9000a02"
PLAN_MARKER = "MARKER_LINE_UNIQUE_50_do_not_inline"


def _long_content() -> str:
    lines = [f"plan line {i:02d}" for i in range(1, 81)]
    lines[49] = PLAN_MARKER
    return "\n".join(lines) + "\n"


def _fixture_detail() -> ExperimentDetailRead:
    return ExperimentDetailRead(
        id=EXP_ID,
        project_id=PROJECT_ID,
        creator_agent_id=CREATOR_ID,
        title="token 优化实验",
        description="验证 CLI 出口 token 快赢项",
        phase="running",
        current_plan_version=3,
        plan_version_count=3,
        review_count=1,
        log_count=2,
        latest_log_summary="I3 落地：topic list 默认 open",
        actions=["complete"],
        blocked_on="none",
        topic_id="1b2c3d4e-5f60-4a1b-8c9d-0e1f2a3b4c5d",
        plan_file_path="map/experiments/compact-fixture/plan.md",
        current_plan={
            "id": "aa11bb22-cc33-4d44-8e55-ff6677889900",
            "experiment_id": EXP_ID,
            "version": 3,
            "content_md": _long_content(),
            "author_agent_id": CREATOR_ID,
            "change_note": "A4 裁剪 plan 回显",
            "created_at": "2026-09-20T10:00:00",
        },
        created_at="2026-09-19T08:00:00",
        updated_at="2026-09-21T09:00:00",
    )


def _fixture_log_response() -> LogCreateResponse:
    body = "\n".join(f"log line {i}" for i in range(1, 78)) + "\n"
    return LogCreateResponse(
        log={
            "id": "77665544-3322-4110-aa99-887766554433",
            "experiment_id": EXP_ID,
            "author_agent_id": CREATOR_ID,
            "summary": "I4 落地",
            "content_md": body,
            "file_path": None,
            "metadata_json": None,
            "created_at": "2026-09-21T10:30:00",
        },
        validation={"warnings": [], "parse_error": None, "plan_keys": [], "valid": True},
    )


class _FakeClient:
    def __init__(self, detail=None, log_response=None):
        self._detail = detail
        self._log_response = log_response

    def get_experiment(self, rid):
        return self._detail

    def create_log(self, rid, payload):
        return self._log_response


class _CM:
    def __init__(self, client):
        self._client = client

    def __enter__(self):
        return self._client

    def __exit__(self, *args):
        return False


def _patch_client_ctx(monkeypatch, client) -> None:
    monkeypatch.setattr(cli_main, "_client_ctx", lambda: _CM(client))
    monkeypatch.setattr(cli_main, "_transport", None)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("MAP_CLI_FORMAT", raising=False)
    monkeypatch.setenv("MAP_TOKEN", "fake")
    monkeypatch.setenv("MAP_API_URL", "http://test")


def _full_yaml(fixture) -> str:
    # runner._print_yaml = typer.echo(yaml.safe_dump(...))，echo 追加一个换行。
    return yaml.safe_dump(
        fixture.model_dump(mode="json"), allow_unicode=True, sort_keys=False
    ) + "\n"


def test_show_default_trims_plan_content(monkeypatch) -> None:
    fixture = _fixture_detail()
    _patch_client_ctx(monkeypatch, _FakeClient(detail=fixture))
    result = CliRunner().invoke(app, ["experiment", "show", "--id", EXP_ID])
    assert result.exit_code == 0, result.output
    assert PLAN_MARKER not in result.stdout
    # 完整 uuid 必须保留：shortid 工作流（list 短前缀 → show 取完整 uuid）
    # 依赖 show 输出（tests/test_cli_shortid.py）。
    assert f"id={EXP_ID}" in result.stdout
    assert "plan: v3 @ map/experiments/compact-fixture/plan.md (80 lines)" in result.stdout
    assert "content_md elided" in result.stdout
    assert "plan_versions: 3" in result.stdout
    assert "reviews: 1  logs: 2" in result.stdout
    assert "[running]" in result.stdout
    # 精简视图显著小于全文
    assert len(result.stdout) < len(_full_yaml(fixture)) - 500


def test_show_full_and_explicit_yaml_equal_legacy(monkeypatch) -> None:
    fixture = _fixture_detail()
    _patch_client_ctx(monkeypatch, _FakeClient(detail=fixture))
    runner = CliRunner()
    legacy = _full_yaml(fixture)
    via_full = runner.invoke(app, ["experiment", "show", "--id", EXP_ID, "--full"])
    via_explicit = runner.invoke(app, ["experiment", "show", "--id", EXP_ID, "--format", "yaml"])
    assert via_full.exit_code == 0, via_full.output
    assert via_explicit.exit_code == 0, via_explicit.output
    assert via_full.stdout == legacy
    assert via_explicit.stdout == legacy
    assert PLAN_MARKER in via_full.stdout


def test_show_json_contract_field_by_field(monkeypatch) -> None:
    import json

    fixture = _fixture_detail()
    _patch_client_ctx(monkeypatch, _FakeClient(detail=fixture))
    result = CliRunner().invoke(app, ["experiment", "show", "--format", "json", "--id", EXP_ID])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["data"] == fixture.model_dump(mode="json")


def test_status_default_trims_and_json_contract(monkeypatch) -> None:
    import json

    fixture = _fixture_detail()
    _patch_client_ctx(monkeypatch, _FakeClient(detail=fixture))
    runner = CliRunner()
    default = runner.invoke(app, ["experiment", "status", "--id", EXP_ID])
    assert default.exit_code == 0, default.output
    # 人性化 hint 行保留（status 命令自身 echo）
    assert "actions: ['complete']" in default.stdout
    assert "blocked_on: none" in default.stdout
    # plan 全文不内联
    assert PLAN_MARKER not in default.stdout
    assert "content_md elided" in default.stdout

    via_json = runner.invoke(app, ["experiment", "status", "--format", "json", "--id", EXP_ID])
    assert via_json.exit_code == 0, via_json.output
    payload = json.loads(via_json.stdout)
    assert payload["data"] == fixture.model_dump(mode="json")

    # 显式 yaml 契约不变：echo 行 + 全文 yaml
    via_yaml = runner.invoke(app, ["experiment", "status", "--id", EXP_ID, "--format", "yaml"])
    assert via_yaml.exit_code == 0, via_yaml.output
    assert PLAN_MARKER in via_yaml.stdout


def test_complete_passes_human_renderer_through(monkeypatch) -> None:
    """complete 走 _run_lifecycle 透传：默认带 renderer，--full 传 None。"""
    captured: dict = {}

    def _stub_run_lifecycle(experiment_id, **kwargs):
        captured.update(kwargs)
        captured["experiment_id"] = experiment_id

    monkeypatch.setattr(experiment_module, "_run_lifecycle", _stub_run_lifecycle)
    runner = CliRunner()
    base = ["experiment", "complete", "--id", EXP_ID, "--summary", "s",
            "--log-file-path", "log.md", "--allow-missing-evidence"]

    result = runner.invoke(app, base)
    assert result.exit_code == 0, result.output
    assert captured["action"] == "complete"
    assert captured["human_renderer"] is not None

    result_full = runner.invoke(app, base + ["--full"])
    assert result_full.exit_code == 0, result_full.output
    assert captured["human_renderer"] is None


def test_log_default_trims_echo_and_json_contract(monkeypatch) -> None:
    import json

    fixture = _fixture_log_response()
    _patch_client_ctx(monkeypatch, _FakeClient(log_response=fixture))
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["experiment", "log", "--id", EXP_ID, "--summary", "I4 落地",
         "--log-file-path", "log.md"],
    )
    assert result.exit_code == 0, result.output
    assert "log: created id=77665544" in result.stdout
    assert "summary: I4 落地" in result.stdout
    assert "content_md: 77 lines stored" in result.stdout
    assert "log line 50" not in result.stdout
    assert "validation: ok" in result.stdout

    via_json = runner.invoke(
        app,
        ["experiment", "log", "--format", "json", "--id", EXP_ID,
         "--summary", "I4 落地", "--log-file-path", "log.md"],
    )
    assert via_json.exit_code == 0, via_json.output
    payload = json.loads(via_json.stdout)
    assert payload["data"] == fixture.model_dump(mode="json")


def test_render_experiment_compact_dict_payload() -> None:
    from cli.experiment_compact_view import render_experiment_compact

    out = render_experiment_compact(_fixture_detail().model_dump(mode="json"))
    assert "plan: v3 @ map/experiments/compact-fixture/plan.md (80 lines)" in out
    assert "content_md elided" in out
    assert PLAN_MARKER not in out


def test_render_experiment_compact_small_plan_no_hint() -> None:
    from cli.experiment_compact_view import render_experiment_compact

    fixture = _fixture_detail()
    fixture = fixture.model_copy(
        update={
            "current_plan": fixture.current_plan.model_copy(
                update={"content_md": "short stub plan"}
            )
        }
    )
    out = render_experiment_compact(fixture)
    assert "content_md elided" not in out
    assert "summary: short stub plan" in out
