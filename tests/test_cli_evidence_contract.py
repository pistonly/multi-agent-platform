"""8ac93d4e I1.d — ``map experiment log`` stdout/stderr contract tests.

Verifies the 4 acceptance (a) cases from
``docs/MAP-EVIDENCE-METADATA.md``:

| Case | input                              | stdout warnings              | stderr                            |
|------|------------------------------------|------------------------------|-----------------------------------|
| 1    | metadata 含全部 evidence_keys      | []                           | silent                            |
| 2    | metadata 含部分                    | [MISSING_EVIDENCE_KEY x N]   | silent                            |
| 3    | metadata=None，plan 有 evidence_keys | [MISSING_EVIDENCE_KEY x N]   | silent                            |
| 4    | plan frontmatter 解析失败          | []                           | [WARN] plan evidence_keys 解析失败: ... |

In all cases the log is still persisted (soft validation never blocks).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from map_client import project_config
from map_client.testing import MAPTestClientTransport
from typer.testing import CliRunner

import cli.main as cli_main
from cli.main import app

pytestmark = pytest.mark.slow


# --- plan frontmatter fixtures (mirror I1.a) ------------------------------

_PLAN_FULL_KEYS = (
    "---\n"
    "evidence_keys:\n"
    "  - pytest_summary\n"
    "  - alembic_current\n"
    "  - api_health\n"
    "---\n"
    "# body\n"
)

_PLAN_BAD_YAML = (
    "---\n"
    "evidence_keys: scalar_not_list\n"
    "---\n"
    "# body\n"
)


# --- fixtures --------------------------------------------------------------


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def patched_cli(monkeypatch, client, auth_headers):
    token = auth_headers["Authorization"].removeprefix("Bearer ")
    monkeypatch.setenv("MAP_TOKEN", token)
    monkeypatch.setenv("MAP_API_URL", "http://test")
    monkeypatch.delenv("MAP_PROJECT_KEY", raising=False)
    monkeypatch.setattr(cli_main, "_transport", MAPTestClientTransport(client))
    monkeypatch.setattr(cli_main, "find_map_dir", lambda *a, **kw: None)
    monkeypatch.setattr(project_config, "find_map_dir", lambda *a, **kw: None)
    monkeypatch.setattr(
        "map_client.config.load_config",
        lambda *a, **kw: {"api_url": "http://test", "token": token, "project_key": None},
    )


def _create_started_experiment(
    client,
    auth_headers: dict[str, str],
    reviewer_headers: dict[str, str],
    project_id: str,
    plan_md: str,
) -> str:
    """Create + review + approve + start an experiment with ``plan_md``.

    Returns the experiment id (running phase, ready to accept logs).
    """
    exp = client.post(
        f"/api/v1/projects/{project_id}/experiments",
        headers=auth_headers,
        json={"title": "I1.d contract test", "plan": {"content_md": plan_md}, "submit_for_review": True},
    ).json()
    exp_id = exp["id"]

    review = client.post(
        f"/api/v1/experiments/{exp_id}/reviews",
        headers=reviewer_headers,
        json={"reasonable_items": ["OK"]},
    ).json()
    for item in review["items"]:
        if item["kind"] == "unreasonable":
            client.patch(
                f"/api/v1/review-items/{item['id']}",
                headers=reviewer_headers,
                json={"status": "resolved"},
            )
    client.post(f"/api/v1/experiments/{exp_id}/approve", headers=auth_headers)
    client.post(f"/api/v1/experiments/{exp_id}/start", headers=auth_headers)
    return exp_id


def _invoke_experiment_log(
    runner: CliRunner,
    exp_id: str,
    summary: str,
    metadata: dict | None,
    tmp_path: Path,
):
    log_file = tmp_path / "log.md"
    log_file.write_text("log body", encoding="utf-8")
    args = [
        "experiment",
        "log",
        "--id",
        exp_id,
        "--summary",
        summary,
        "--file",
        str(log_file),
    ]
    if metadata is not None:
        metadata_file = tmp_path / "metadata.yaml"
        metadata_file.write_text(yaml.safe_dump(metadata), encoding="utf-8")
        args.extend(["--metadata", str(metadata_file)])
    return runner.invoke(app, args)


# --- 4 cases ---------------------------------------------------------------


def test_case_1_all_declared_emits_empty_warnings_no_stderr(
    runner, patched_cli, client, auth_headers, reviewer, project, tmp_path: Path
) -> None:
    """Case 1: metadata covers all evidence_keys → stdout warnings=[], stderr silent."""
    exp_id = _create_started_experiment(
        client, auth_headers, reviewer["headers"], project["id"], _PLAN_FULL_KEYS
    )
    result = _invoke_experiment_log(
        runner,
        exp_id,
        "case1 full",
        {"pytest_summary": "12 passed", "alembic_current": "035 (head)", "api_health": "200"},
        tmp_path,
    )
    assert result.exit_code == 0, result.output

    payload = yaml.safe_load(result.stdout)
    assert "validation" in payload, payload
    assert payload["validation"]["warnings"] == []
    assert payload["validation"]["parse_error"] is None
    assert payload["validation"]["valid"] is True
    assert payload["log"]["summary"] == "case1 full"

    stderr = result.stderr or ""
    assert "plan evidence_keys 解析失败" not in stderr
    # No legacy warnings either
    assert "MISSING_EVIDENCE_KEY" not in stderr


def test_case_2_partial_declared_emits_warnings_no_stderr(
    runner, patched_cli, client, auth_headers, reviewer, project, tmp_path: Path
) -> None:
    """Case 2: metadata covers 1 of 3 → stdout 2 warnings, stderr silent."""
    exp_id = _create_started_experiment(
        client, auth_headers, reviewer["headers"], project["id"], _PLAN_FULL_KEYS
    )
    result = _invoke_experiment_log(
        runner, exp_id, "case2 partial", {"pytest_summary": "ok"}, tmp_path
    )
    assert result.exit_code == 0, result.output

    payload = yaml.safe_load(result.stdout)
    warnings = payload["validation"]["warnings"]
    assert len(warnings) == 2
    codes = {w["code"] for w in warnings}
    assert codes == {"MISSING_EVIDENCE_KEY"}
    missing = {w["missing_key"] for w in warnings}
    assert missing == {"alembic_current", "api_health"}
    assert payload["validation"]["parse_error"] is None
    assert payload["validation"]["valid"] is True

    stderr = result.stderr or ""
    assert "plan evidence_keys 解析失败" not in stderr


def test_case_3_no_metadata_emits_all_warnings_no_stderr(
    runner, patched_cli, client, auth_headers, reviewer, project, tmp_path: Path
) -> None:
    """Case 3: metadata=None → stdout N warnings for all plan keys, stderr silent."""
    exp_id = _create_started_experiment(
        client, auth_headers, reviewer["headers"], project["id"], _PLAN_FULL_KEYS
    )
    result = _invoke_experiment_log(runner, exp_id, "case3 none", None, tmp_path)
    assert result.exit_code == 0, result.output

    payload = yaml.safe_load(result.stdout)
    warnings = payload["validation"]["warnings"]
    assert len(warnings) == 3
    missing = {w["missing_key"] for w in warnings}
    assert missing == {"pytest_summary", "alembic_current", "api_health"}
    assert all(w["code"] == "MISSING_EVIDENCE_KEY" for w in warnings)
    assert payload["validation"]["parse_error"] is None
    assert payload["validation"]["valid"] is True

    stderr = result.stderr or ""
    assert "plan evidence_keys 解析失败" not in stderr


def test_case_4_plan_yaml_parse_error_emits_stderr_warn(
    runner, patched_cli, client, auth_headers, reviewer, project, tmp_path: Path
) -> None:
    """Case 4: plan frontmatter is un-parseable → stdout warnings=[], stderr [WARN]."""
    exp_id = _create_started_experiment(
        client, auth_headers, reviewer["headers"], project["id"], _PLAN_BAD_YAML
    )
    result = _invoke_experiment_log(
        runner, exp_id, "case4 bad-yaml", {"pytest_summary": "ok"}, tmp_path
    )
    assert result.exit_code == 0, result.output

    payload = yaml.safe_load(result.stdout)
    assert payload["validation"]["warnings"] == []
    assert payload["validation"]["parse_error"] is not None
    assert payload["validation"]["valid"] is True
    # Log still persisted
    assert payload["log"]["summary"] == "case4 bad-yaml"

    stderr = result.stderr or ""
    assert "[WARN] plan evidence_keys 解析失败:" in stderr
    # Stderr must contain the parse error message body
    assert "evidence_keys" in stderr or "list" in stderr


# --- regression: existing behavior preserved --------------------------------


def test_experiment_log_still_saves_on_warnings(
    runner, patched_cli, client, auth_headers, reviewer, project, tmp_path: Path
) -> None:
    """Soft validation invariant: warnings do not prevent log save.

    The persisted log must show up in ``GET /experiments/{id}/logs``.
    """
    exp_id = _create_started_experiment(
        client, auth_headers, reviewer["headers"], project["id"], _PLAN_FULL_KEYS
    )
    result = _invoke_experiment_log(
        runner, exp_id, "soft save test", {"pytest_summary": "ok"}, tmp_path
    )
    assert result.exit_code == 0, result.output

    # Verify log is in the listing
    list_resp = client.get(
        f"/api/v1/experiments/{exp_id}/logs", headers=auth_headers
    )
    assert list_resp.status_code == 200
    logs = list_resp.json()
    assert len(logs) == 1
    assert logs[0]["summary"] == "soft save test"
    assert logs[0]["metadata_json"] == {"pytest_summary": "ok"}


def test_experiment_log_no_frontmatter_plan_silent(
    runner, patched_cli, client, auth_headers, reviewer, project, tmp_path: Path
) -> None:
    """Plan without frontmatter → validation empty, stderr silent (regression)."""
    exp_id = _create_started_experiment(
        client, auth_headers, reviewer["headers"], project["id"], "# plain plan\n"
    )
    result = _invoke_experiment_log(
        runner, exp_id, "silent test", {"pytest_summary": "ok"}, tmp_path
    )
    assert result.exit_code == 0, result.output

    payload = yaml.safe_load(result.stdout)
    assert payload["validation"]["warnings"] == []
    assert payload["validation"]["parse_error"] is None
    assert payload["validation"]["plan_keys"] == []

    stderr = result.stderr or ""
    assert "plan evidence_keys 解析失败" not in stderr


def test_experiment_log_yaml_output_is_machine_parseable(
    runner, patched_cli, client, auth_headers, reviewer, project, tmp_path: Path
) -> None:
    """Default ``--format yaml`` output is parseable YAML with wrapper shape (I1.d).

    CLI success output is YAML (regardless of ``--format`` flag, which only
    affects error envelope rendering). The wrapper {log, validation} shape
    must round-trip through yaml.safe_load.
    """
    exp_id = _create_started_experiment(
        client, auth_headers, reviewer["headers"], project["id"], _PLAN_FULL_KEYS
    )
    log_file = tmp_path / "log.md"
    log_file.write_text("log body", encoding="utf-8")
    metadata_file = tmp_path / "metadata.yaml"
    metadata_file.write_text(yaml.safe_dump({"pytest_summary": "ok"}), encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "experiment",
            "log",
            "--id",
            exp_id,
            "--summary",
            "yaml format",
            "--file",
            str(log_file),
            "--metadata",
            str(metadata_file),
        ],
    )
    assert result.exit_code == 0, f"stdout={result.stdout!r}, stderr={result.stderr!r}"

    payload = yaml.safe_load(result.stdout)
    assert "log" in payload
    assert "validation" in payload
    assert len(payload["validation"]["warnings"]) == 2
    assert payload["log"]["summary"] == "yaml format"
