"""v0.12 M55: actionable error envelope (E3/E4/E6/E8 fixes).

Pins the acceptance from ``map/experiments/m55-actionable-error-envelope``
plan v2 (review r1/r2):

* M55A — FastAPI ``RequestValidationError`` (422) keeps the default
  ``detail`` array and appends ``error_code`` / ``hint`` / ``retryable``
  (same field names as ``StateTransitionError``); the experiments-create
  endpoint embeds a minimal copy-pasteable payload, others degrade to the
  missing-field list.
* M55B — frontmatter gate failure hints carry a compact (~15 lines)
  fenced YAML template the agent can copy verbatim.
* M55C — ``dependencies: []`` passes (explicit no-deps, kills the
  ``- none`` sentinel); missing key still blocks; the other three fields
  keep their non-empty requirement.
* M55D — E8: slim ``--plan-file-path`` create no longer trips the server
  content gate, and the CLI local lint covers BOTH forms (good slim plan
  passes, bad slim plan is blocked with exit 2 before any request).
* M55E — CLI yaml error output renders Hint / Docs / Recover lines; the
  JSON error envelope shape is untouched (M54C contract).
"""

from __future__ import annotations

import json
import uuid
from urllib.parse import urlparse

import httpx
import pytest
from typer.testing import CliRunner

import cli.main as cli_main
import cli.runner as cli_runner_mod  # T23: helpers moved to runner
from cli.main import app
from server.services.errors import StateTransitionError
from server.services.plan_marker_service import (
    assert_plan_frontmatter_ok,
    validate_plan_frontmatter,
)
from tests._frontmatter import make_valid_plan

PROJECT_ID = uuid.uuid4()
AGENT_ID = uuid.uuid4()
EXP_ID = uuid.UUID("84cccb2e-2a69-4339-9dfd-c3df1bf23d31")
_TS = "2026-08-16T00:00:00Z"


# ---- M55A: 422 handler (API level) ------------------------------------------


def test_422_keeps_detail_array_and_adds_envelope_fields(client, auth_headers, project):
    resp = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={"title": "no-plan-field"},
    )
    assert resp.status_code == 422, resp.text
    body = resp.json()
    # additive contract: FastAPI's default detail array survives byte-compat
    assert isinstance(body["detail"], list) and body["detail"]
    assert body["error_code"] == "request_validation_error"
    assert body["retryable"] is False
    assert "missing fields: plan" in body["hint"]


def test_422_experiments_endpoint_hint_embeds_minimal_payload(
    client, auth_headers, project
):
    resp = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={"title": "no-plan-field"},
    )
    hint = resp.json()["hint"]
    assert "minimal payload for POST /api/v1/projects/" in hint
    assert "plan" in hint  # the example shows the missing key in context


def test_422_other_endpoints_degrade_to_field_list(client, auth_headers):
    # v0.15 M62 前 feedback 端点承担本场景，退役（410）后换 inbound-event
    resp = client.post(
        "/api/v1/agents/me/inbound-events", headers=auth_headers, json={"wrong": "shape"}
    )
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["error_code"] == "request_validation_error"
    # degrade path: field list only, no endpoint-specific payload example
    assert "missing fields: event_id" in body["hint"]
    assert "minimal payload" not in body["hint"]


# ---- M55B: frontmatter template hint (service level) ------------------------


def test_missing_frontmatter_hint_contains_copyable_template():
    with pytest.raises(StateTransitionError) as ei:
        assert_plan_frontmatter_ok("## no frontmatter")
    hint = ei.value.hint
    assert hint is not None
    # template is fenced, carries all 4 fields and annotates what is missing
    assert "\n---\n" in hint
    for field in ("title:", "acceptance:", "evidence_keys:", "dependencies:"):
        assert field in hint
    assert "missing:" in hint  # all four flagged when block absent


def test_partial_fields_annotated_in_hint():
    with pytest.raises(StateTransitionError) as ei:
        assert_plan_frontmatter_ok("---\ntitle: t\n---\nbody")
    assert "dependencies" in ei.value.hint
    assert "missing: acceptance, dependencies, evidence_keys" in ei.value.hint


def test_hint_template_is_compact():
    """Review r2 constraint: ~15 lines max — the hint lands in stderr/logs."""
    with pytest.raises(StateTransitionError) as ei:
        assert_plan_frontmatter_ok(None)
    line_count = len(ei.value.hint.splitlines())
    assert line_count <= 15, f"hint grew to {line_count} lines"


# ---- M55C: dependencies empty list (service level) ---------------------------


def test_dependencies_empty_list_passes():
    plan = make_valid_plan(dependencies=[])
    result = validate_plan_frontmatter(plan)
    deps_warnings = [
        w for w in result.warnings if w.field == "dependencies"
    ]
    assert deps_warnings == []
    assert "dependencies" in result.fields_present
    # hard gate also passes — explicit [] means no deps, no "- none" sentinel
    assert_plan_frontmatter_ok(plan)


def test_dependencies_missing_key_still_blocks():
    plan = "---\ntitle: t\nacceptance: [a]\nevidence_keys: [e]\n---\nbody"
    with pytest.raises(StateTransitionError):
        assert_plan_frontmatter_ok(plan)


def test_other_fields_empty_list_still_blocks():
    plan = make_valid_plan(acceptance=[])
    with pytest.raises(StateTransitionError) as ei:
        assert_plan_frontmatter_ok(plan)
    assert "acceptance" in str(ei.value)


# ---- M55D: E8 slim-form gate (API level) -------------------------------------


def test_create_experiment_slim_form_passes_content_gate(
    client, auth_headers, project
):
    """E8 regression: file_path form has no content_md — server must not
    demand frontmatter it cannot read (the CLI local lint is the front
    gate for slim plans)."""
    resp = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={
            "title": "slim-e8",
            "plan": {"file_path": "map/experiments/x/plan.md"},
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["title"] == "slim-e8"
    assert body["plan_file_path"] == "map/experiments/x/plan.md"
    # plan_versions.content_md is NOT NULL — the slim create stores a
    # self-describing stub instead of crashing with a mislabeled 409.
    detail = client.get(f"/api/v1/experiments/{body['id']}", headers=auth_headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["current_plan"]["content_md"].startswith(
        "<!-- slim create: plan content lives in map/experiments/x/plan.md"
    )


def test_create_experiment_full_form_still_enforces_gate(
    client, auth_headers, project
):
    resp = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={"title": "bad", "plan": {"content_md": "## no frontmatter"}},
    )
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["error_code"] == "STATE_MACHINE_PLAN_MARKER_MISSING"
    assert "---" in body["hint"]  # M55B template rides along


# ---- M55D + M55E: CLI level (CliRunner + stub transport) ---------------------


class CreateStubTransport:
    """Records create POSTs; returns a 201 summary; everything else 404."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, str(request.url)))
        path = urlparse(str(request.url)).path
        if request.method == "GET" and path.endswith("/agents/me"):
            # create 成功后取 creator persona（cli.experiment_fs 写 index.md 用）
            return httpx.Response(
                200,
                json={
                    "id": str(AGENT_ID),
                    "name": "host-agent",
                    "role": "admin",
                    "project_id": str(PROJECT_ID),
                    "created_at": _TS,
                },
            )
        if request.method == "POST" and path.endswith("/experiments"):
            return httpx.Response(
                201,
                json={
                    "id": str(EXP_ID),
                    "project_id": str(PROJECT_ID),
                    "creator_agent_id": str(AGENT_ID),
                    "title": "slim",
                    "description": None,
                    "phase": "draft",
                    "current_plan_version": 1,
                    "created_at": _TS,
                    "updated_at": _TS,
                },
            )
        return httpx.Response(404, json={"detail": "unstubbed"})


class ErrorStubTransport:
    """Serves one 422 with hint/error_code; everything else 404."""

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        path = urlparse(str(request.url)).path
        if request.method == "GET" and path.endswith(f"/experiments/{EXP_ID}"):
            return httpx.Response(
                422,
                json={
                    "detail": "Plan frontmatter is missing required fields: title",
                    "error_code": "STATE_MACHINE_PLAN_MARKER_MISSING",
                    "hint": "---\ntitle: \"实验标题\"\n---",
                    "retryable": False,
                },
            )
        return httpx.Response(404, json={"detail": "unstubbed"})


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def stub_env(monkeypatch):
    def _install(transport: httpx.BaseTransport) -> None:
        monkeypatch.setattr(cli_main, "_transport", transport)
        monkeypatch.setattr(
            cli_runner_mod, "_resolve_project", lambda client, p, k: PROJECT_ID
        )
        monkeypatch.setenv("MAP_TOKEN", "fake")
        monkeypatch.setenv("MAP_API_URL", "http://test")
        monkeypatch.delenv("MAP_CLI_FORMAT", raising=False)
        # 实验 e7244a91 A5 后 FS 解析统一走 ProjectContext（运行时读
        # cli.main.find_map_dir）。只补丁 cli.experiment_fs.find_map_dir
        # 这个死绑定拦不住 create 的 FS 写回，会把 stub 泄漏进真实
        # workspace（历史泄漏：map/experiments/slim/）——两处都补。
        monkeypatch.setattr(cli_main, "find_map_dir", lambda *args, **kwargs: None)
        monkeypatch.setattr(
            "cli.experiment_fs.find_map_dir", lambda *args, **kwargs: None
        )

    return _install


def test_cli_slim_good_plan_passes_local_lint(stub_env, runner, tmp_path):
    plan = tmp_path / "plan.md"
    plan.write_text(make_valid_plan(title="slim"), encoding="utf-8")
    transport = CreateStubTransport()
    stub_env(transport)

    result = runner.invoke(
        app,
        ["experiment", "create", "--title", "slim", "--plan-file-path", str(plan)],
    )
    assert result.exit_code == 0, result.output
    posts = [c for c in transport.calls if c[0] == "POST"]
    assert len(posts) == 1  # local lint passed, request went out


def test_cli_slim_bad_plan_blocked_by_local_lint(stub_env, runner, tmp_path):
    """Review r1 acceptance: a frontmatter-less slim plan must NOT reach
    the server — the local pre-check is the front gate for slim forms."""
    plan = tmp_path / "bad.md"
    plan.write_text("## no frontmatter", encoding="utf-8")
    transport = CreateStubTransport()
    stub_env(transport)

    result = runner.invoke(
        app,
        ["experiment", "create", "--title", "slim", "--plan-file-path", str(plan)],
    )
    assert result.exit_code == 2
    assert "frontmatter lint failed" in result.output
    assert transport.calls == []  # blocked before any HTTP request


def test_cli_slim_bad_plan_can_force_bypass(stub_env, runner, tmp_path):
    """--force-lint-bypass semantics unchanged for slim forms too."""
    plan = tmp_path / "bad.md"
    plan.write_text("## no frontmatter", encoding="utf-8")
    transport = CreateStubTransport()
    stub_env(transport)

    result = runner.invoke(
        app,
        [
            "experiment", "create", "--title", "slim",
            "--plan-file-path", str(plan), "--force-lint-bypass",
        ],
    )
    assert result.exit_code == 0, result.output
    posts = [c for c in transport.calls if c[0] == "POST"]
    assert len(posts) == 1  # 绕过 lint 后恰好一次 create POST（get_me 不计）


def test_cli_yaml_error_renders_hint_block(stub_env, runner):
    stub_env(ErrorStubTransport())

    result = runner.invoke(app, ["experiment", "show", "--id", str(EXP_ID)])
    assert result.exit_code == 1
    err = result.stderr
    assert "[error_code=STATE_MACHINE_PLAN_MARKER_MISSING]" in err
    assert "Hint: ---" in err  # multi-line hint flows verbatim
    assert 'title: "实验标题"' in err


def test_cli_json_error_envelope_shape_unchanged(stub_env, runner):
    """M54C contract: JSON error mode emits ONLY the envelope on stderr."""
    stub_env(ErrorStubTransport())

    result = runner.invoke(
        app, ["experiment", "show", "--id", str(EXP_ID), "--format", "json"]
    )
    assert result.exit_code == 1
    envelope = json.loads(result.stderr)
    assert envelope["ok"] is False
    err = envelope["error"]
    assert err["error_code"] == "STATE_MACHINE_PLAN_MARKER_MISSING"
    assert err["hint"].startswith("---")
    assert err["recovery_command"] == err["hint"]  # mirror by design
    assert set(err) <= {
        "error_code", "message", "hint",
        "docs_url", "retryable", "recovery_command",
    }
