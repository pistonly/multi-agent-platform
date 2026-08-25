"""v0.12 M54C: ``--format json`` output/schema alignment guard (E3/E4 fix).

Pins the contract documented in ``docs/cli-json-output.md``:

* success envelope on stdout: ``{"ok": true, "data": <payload>}`` — and
  stdout contains NOTHING else (``experiment status`` used to prepend
  human hint lines, breaking ``jq`` on the first line);
* error envelope on stderr: ``{"ok": false, "error": {...}}`` matching
  ``cli.main.CLIErrorEnvelope`` (``extra="forbid"``);
* ``data`` payloads deserialize straight back into the ``map_types``
  models (uuid as full 36-char lowercase string, datetime as ISO8601,
  field names identical to the REST API);
* ``experiment pre-complete`` no longer nests an ``ok`` inside ``data``.

Drift-guard design: stub payloads are **hand-written frozen literals**
(never produced via ``model_dump``), so renaming/retyping a field in
``map_types`` fails ``model_validate`` here even though the SDK's own
round-trips would stay green.
"""

from __future__ import annotations

import json
import re
import uuid
from urllib.parse import urlparse

import httpx
import pytest
from map_types.schemas import (
    ExperimentDetailRead,
    ExperimentLogRead,
    ExperimentSummaryRead,
    LogCreateResponse,
)
from typer.testing import CliRunner

import cli.main as cli_main
from cli.main import CLIErrorEnvelope, app

PROJECT_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
AGENT_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
EXP_ID = uuid.UUID("f4ef8cb2-a9af-46d1-9259-ca728fd33428")

_UUID36 = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_ISO8601 = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})?$")

# ---- frozen REST payload literals (drift guard — do NOT generate via model_dump)


def _summary(eid: str = str(EXP_ID)) -> dict:
    return {
        "id": eid,
        "project_id": str(PROJECT_ID),
        "creator_agent_id": str(AGENT_ID),
        "title": "frozen literal experiment",
        "description": None,
        "phase": "running",
        "current_plan_version": 2,
        "created_at": "2026-08-15T03:12:45Z",
        "updated_at": "2026-08-15T04:00:00Z",
    }


def _detail() -> dict:
    return {
        **_summary(),
        "actions": ["complete"],
        "blocked_on": None,
        "phase_owner": "host",
    }


_LOG_READ = {
    "id": "33333333-3333-3333-3333-333333333333",
    "experiment_id": str(EXP_ID),
    "author_agent_id": str(AGENT_ID),
    "summary": "frozen log summary",
    "content_md": "# frozen log body",
    "metadata_json": None,
    "created_at": "2026-08-15T05:00:00Z",
}

_LOG_CREATE = {"log": dict(_LOG_READ), "validation": {}}


class StubTransport:
    """Serves the frozen literals above; unstubbed paths 404."""

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        # Match on path suffix — the SDK prefixes /api/v1 (base_url=root).
        path = urlparse(str(request.url)).path
        if request.method == "GET" and path.endswith("/experiments"):
            return httpx.Response(
                200,
                headers={"X-Total-Count": "1"},
                content=json.dumps([_summary()]).encode(),
            )
        if request.method == "GET" and path.endswith(f"/experiments/{EXP_ID}/logs"):
            return httpx.Response(200, json=[dict(_LOG_READ)])
        if request.method == "POST" and path.endswith(f"/experiments/{EXP_ID}/logs"):
            return httpx.Response(200, json=_LOG_CREATE)
        if request.method == "GET" and path.endswith(f"/experiments/{EXP_ID}"):
            return httpx.Response(200, json=_detail())
        return httpx.Response(404, json={"detail": "unstubbed"})


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def stub_env(monkeypatch):
    monkeypatch.setattr(cli_main, "_transport", StubTransport())
    monkeypatch.setattr(
        cli_main, "_resolve_project", lambda client, p, k: PROJECT_ID
    )
    # Isolate from the real repo map/experiments merge (FS list extras).
    monkeypatch.setattr("cli.experiment_fs.workspace_root", lambda: None)
    monkeypatch.setenv("MAP_TOKEN", "fake")
    monkeypatch.setenv("MAP_API_URL", "http://test")
    monkeypatch.delenv("MAP_CLI_FORMAT", raising=False)


def _envelope(result) -> dict:
    """Parse the single JSON document the CLI must have put on stdout.

    用 ``result.stdout`` 而非 ``result.output``：click 8.4 的
    ``Result.output`` 混含 stderr，契约只约束 stdout（docs/cli-json-output.md
    "stdout 只含这一份 JSON"），stderr 的人类提示行不应使解析失败。
    """
    return json.loads(result.stdout)


# ---- success envelope + schema round-trips ----------------------------------


def test_show_data_roundtrips_experiment_detail(stub_env, runner):
    result = runner.invoke(app, ["experiment", "show", "--id", str(EXP_ID), "--format", "json"])
    assert result.exit_code == 0, result.output

    env = _envelope(result)
    assert env["ok"] is True
    # frozen literal → CLI → back into the map_types model (drift guard)
    detail = ExperimentDetailRead.model_validate(env["data"])
    assert detail.id == EXP_ID
    assert detail.phase.value == "running"

    data = env["data"]
    assert _UUID36.fullmatch(data["id"])
    assert _UUID36.fullmatch(data["project_id"])
    assert _ISO8601.fullmatch(data["created_at"])
    assert _ISO8601.fullmatch(data["updated_at"])
    assert data["description"] is None  # missing optional fields stay as null keys
    assert data["title"] == "frozen literal experiment"


def test_list_data_is_array_of_summaries(stub_env, runner):
    result = runner.invoke(app, ["experiment", "list", "--format", "json"])
    assert result.exit_code == 0, result.output

    env = _envelope(result)
    assert env["ok"] is True
    data = env["data"]
    assert isinstance(data, list) and len(data) == 1
    for item in data:
        ExperimentSummaryRead.model_validate(item)
    assert data[0]["id"] == str(EXP_ID)


def test_logs_data_roundtrips_experiment_log(stub_env, runner):
    result = runner.invoke(app, ["experiment", "logs", "--id", str(EXP_ID), "--format", "json"])
    assert result.exit_code == 0, result.output

    env = _envelope(result)
    assert env["ok"] is True
    assert isinstance(env["data"], list) and len(env["data"]) == 1
    entry = ExperimentLogRead.model_validate(env["data"][0])
    assert entry.summary == "frozen log summary"
    assert _UUID36.fullmatch(env["data"][0]["id"])


def test_log_create_data_is_log_create_response_wrapper(stub_env, runner, tmp_path, monkeypatch):
    # typer 0.27 的 CliRunner 移除了 isolated_filesystem；用 pytest 标准
    # tmp_path + chdir 等价提供临时 cwd（写 log.md 相对路径）。
    monkeypatch.chdir(tmp_path)
    with open("log.md", "w") as fh:
        fh.write("# body")
    result = runner.invoke(
        app,
        [
            "experiment", "log", "--id", str(EXP_ID),
            "--summary", "s", "--file", "log.md", "--format", "json",
        ],
    )
    assert result.exit_code == 0, result.output

    env = _envelope(result)
    assert env["ok"] is True
    payload = LogCreateResponse.model_validate(env["data"])
    assert payload.log.id == uuid.UUID(_LOG_READ["id"])
    assert "validation" in env["data"]


def test_status_json_stdout_is_pure_json(stub_env, runner):
    """M54C: ``status --format json`` must not prepend human hint lines
    (they used to break ``jq`` on the very first stdout line)."""
    result = runner.invoke(app, ["experiment", "status", "--id", str(EXP_ID), "--format", "json"])
    assert result.exit_code == 0, result.output

    env = json.loads(result.stdout)  # ENTIRE stdout — pollution guard
    assert env["ok"] is True
    detail = ExperimentDetailRead.model_validate(env["data"])
    assert detail.id == EXP_ID
    for key in ("actions", "blocked_on", "phase_owner"):
        assert key in env["data"]


def test_status_default_format_keeps_human_hints(stub_env, runner):
    """Plan v2 风险与对策: M54C 只动 json 分支，默认（yaml）输出不变."""
    result = runner.invoke(app, ["experiment", "status", "--id", str(EXP_ID)])
    assert result.exit_code == 0, result.output
    assert "actions:" in result.output
    assert "phase_owner:" in result.output


def test_pre_complete_data_has_no_nested_ok(stub_env, runner, tmp_path, monkeypatch):
    """M54C: the envelope carries ``ok``; a data-level ``ok`` collided
    with the contract (docs/cli-json-output.md)."""
    # 同上：typer 0.27 CliRunner 无 isolated_filesystem，换 pytest 临时目录。
    monkeypatch.chdir(tmp_path)
    with open("meta.yaml", "w") as fh:
        fh.write("api_health: ok\n")
    result = runner.invoke(
        app,
        [
            "experiment", "pre-complete", "--id", str(EXP_ID),
            "--metadata", "meta.yaml", "--format", "json",
        ],
    )
    assert result.exit_code == 0, result.output

    env = _envelope(result)
    assert env["ok"] is True
    data = env["data"]
    assert set(data) == {"experiment_id", "phase", "current_plan_version", "evidence_keys"}
    assert data["experiment_id"] == str(EXP_ID)
    assert data["phase"] == "running"
    assert data["evidence_keys"] == ["api_health"]


# ---- error envelope ----------------------------------------------------------


def test_error_envelope_on_stderr_matches_cli_error_envelope(stub_env, runner):
    result = runner.invoke(
        app, ["experiment", "show", "--id", "99999999-9999-4999-8999-999999999999", "--format", "json"]
    )
    assert result.exit_code == 1

    env = json.loads(result.stderr)  # stderr carries ONLY the envelope in json mode
    assert env["ok"] is False
    # extra="forbid" → any field-name drift in the envelope fails validation
    parsed = CLIErrorEnvelope.model_validate(env["error"])
    assert parsed.message


# ---- schema drift pin (doc ↔ map_types) --------------------------------------


def test_documented_core_fields_exist_in_map_types():
    """docs/cli-json-output.md「序列化约定」 pins these fields; removing
    one from map_types must fail here, not in a downstream script."""
    for model in (ExperimentDetailRead, ExperimentSummaryRead):
        assert {
            "id", "project_id", "creator_agent_id", "title",
            "description", "phase", "current_plan_version",
            "created_at", "updated_at",
        } <= set(model.model_fields)
    assert {"id", "experiment_id", "author_agent_id", "summary", "content_md",
            "metadata_json", "created_at"} <= set(ExperimentLogRead.model_fields)
    assert {"log", "validation"} <= set(LogCreateResponse.model_fields)
