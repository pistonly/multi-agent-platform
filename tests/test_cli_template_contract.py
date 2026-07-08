"""b72d0542 I1.c — ``map experiment complete`` stdout/stderr contract tests
for the 4-段 template soft validation.

Mirrors ``test_cli_evidence_contract.py`` (8ac93d4e I1.d) but exercises
the ``template_validation`` wrapper returned by the complete endpoint.

| Case | body content_md                                | template_validation.warnings         | stderr                                       |
|------|------------------------------------------------|--------------------------------------|----------------------------------------------|
| 1    | 完整 4 段 + 实施 log 含合法链接                | []                                   | silent                                       |
| 2    | 缺 summary                                     | [MISSING_TEMPLATE_SECTION x 1]       | [WARN] template: MISSING_TEMPLATE_SECTION    |
| 3    | 缺 acceptance                                  | [MISSING_TEMPLATE_SECTION x 1]       | [WARN] template: MISSING_TEMPLATE_SECTION    |
| 4    | 实施 log 含合法 + 不平衡 fragment              | [MALFORMED_MARKDOWN_LINK]            | [WARN] template: MALFORMED_MARKDOWN_LINK     |

In all cases the complete call succeeds, the experiment transitions to
``result_review``, and ``template_validation.valid`` is True.
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


# --- content_md fixtures ---------------------------------------------------

_COMPLETE_RESULT_BODY = (
    "# 实验 b72d0542 result\n\n"
    "## summary\n"
    "4 段模板就绪\n\n"
    "## 实施 log\n"
    "- [W45 I1.a](.map/generated-plans/experiment-b72d0542-i1-a-log.md)\n"
    "- [W46 I1.b](.map/generated-plans/experiment-b72d0542-i1-b-log.md)\n\n"
    "## 风险\n"
    "- 风险 1\n\n"
    "## acceptance\n"
    "- [x] (a) 4 段模板 + Pydantic schema 校验\n"
)


def _missing_summary_body() -> str:
    return (
        "## 实施 log\n"
        "- [W45](file.md)\n\n"
        "## 风险\n"
        "x\n\n"
        "## acceptance\n"
        "- [x] (a)\n"
    )


def _missing_acceptance_body() -> str:
    return (
        "## summary\n"
        "x\n\n"
        "## 实施 log\n"
        "- [W45](file.md)\n\n"
        "## 风险\n"
        "x\n"
    )


def _malformed_link_body() -> str:
    return (
        "## summary\n"
        "x\n\n"
        "## 实施 log\n"
        "- [valid](path.md)\n"
        "- text [broken without close\n\n"
        "## 风险\n"
        "x\n\n"
        "## acceptance\n"
        "- [x] (a)\n"
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
) -> str:
    """Create + review + approve + start an experiment.

    The plan is plain markdown (no frontmatter) — I1.c only cares about
    the complete path, not evidence metadata structure.
    """
    exp = client.post(
        f"/api/v1/projects/{project_id}/experiments",
        headers=auth_headers,
        json={
            "title": "I1.c template contract test",
            "plan": {"content_md": "# plain plan\n"},
            "submit_for_review": True,
        },
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


def _invoke_experiment_complete(
    runner: CliRunner,
    exp_id: str,
    summary: str,
    body: str,
    tmp_path: Path,
):
    log_file = tmp_path / "result.md"
    log_file.write_text(body, encoding="utf-8")
    metadata_file = tmp_path / "metadata.yaml"
    metadata_file.write_text(
        yaml.safe_dump({"pytest_summary": "test evidence stub"}),
        encoding="utf-8",
    )
    return runner.invoke(
        app,
        [
            "experiment",
            "complete",
            "--id",
            exp_id,
            "--summary",
            summary,
            "--file",
            str(log_file),
            "--metadata",
            str(metadata_file),
        ],
    )


# --- 4 cases ---------------------------------------------------------------


def test_case_1_all_sections_present_emits_no_warnings_no_stderr(
    runner, patched_cli, client, auth_headers, reviewer, project, tmp_path: Path
) -> None:
    """Case 1: 完整 4 段 + 合法 log 链接 → template_validation.warnings=[],
    stderr silent."""
    exp_id = _create_started_experiment(
        client, auth_headers, reviewer["headers"], project["id"]
    )
    result = _invoke_experiment_complete(
        runner, exp_id, "case1 full", _COMPLETE_RESULT_BODY, tmp_path
    )
    assert result.exit_code == 0, f"stdout={result.stdout!r}, stderr={result.stderr!r}"

    payload = yaml.safe_load(result.stdout)
    assert "template_validation" in payload, payload
    assert payload["template_validation"]["warnings"] == []
    assert payload["template_validation"]["sections_present"] == [
        "summary",
        "实施 log",
        "风险",
        "acceptance",
    ]
    assert payload["template_validation"]["log_link_count"] == 2
    assert payload["template_validation"]["valid"] is True
    assert payload["phase"] == "result_review"

    stderr = result.stderr or ""
    assert "[WARN] template:" not in stderr


def test_case_2_missing_summary_emits_warning(
    runner, patched_cli, client, auth_headers, reviewer, project, tmp_path: Path
) -> None:
    """Case 2: 缺 summary → template_validation.warnings has
    MISSING_TEMPLATE_SECTION(section=summary); stderr has matching line."""
    exp_id = _create_started_experiment(
        client, auth_headers, reviewer["headers"], project["id"]
    )
    result = _invoke_experiment_complete(
        runner, exp_id, "case2 missing-summary", _missing_summary_body(), tmp_path
    )
    assert result.exit_code == 0, f"stdout={result.stdout!r}, stderr={result.stderr!r}"

    payload = yaml.safe_load(result.stdout)
    warnings = payload["template_validation"]["warnings"]
    codes = [w["code"] for w in warnings]
    assert "MISSING_TEMPLATE_SECTION" in codes
    sections = {
        w["section"] for w in warnings if w["code"] == "MISSING_TEMPLATE_SECTION"
    }
    assert "summary" in sections
    assert payload["template_validation"]["valid"] is True
    assert payload["phase"] == "result_review"

    stderr = result.stderr or ""
    assert "[WARN] template: MISSING_TEMPLATE_SECTION" in stderr
    assert "(section=summary)" in stderr


def test_case_3_missing_acceptance_emits_warning(
    runner, patched_cli, client, auth_headers, reviewer, project, tmp_path: Path
) -> None:
    """Case 3: 缺 acceptance → MISSING_TEMPLATE_SECTION(section=acceptance)
    + stderr line."""
    exp_id = _create_started_experiment(
        client, auth_headers, reviewer["headers"], project["id"]
    )
    result = _invoke_experiment_complete(
        runner, exp_id, "case3 missing-acceptance", _missing_acceptance_body(), tmp_path
    )
    assert result.exit_code == 0, f"stdout={result.stdout!r}, stderr={result.stderr!r}"

    payload = yaml.safe_load(result.stdout)
    warnings = payload["template_validation"]["warnings"]
    sections = {
        w["section"] for w in warnings if w["code"] == "MISSING_TEMPLATE_SECTION"
    }
    assert "acceptance" in sections
    assert payload["template_validation"]["valid"] is True
    assert payload["phase"] == "result_review"

    stderr = result.stderr or ""
    assert "[WARN] template: MISSING_TEMPLATE_SECTION" in stderr
    assert "(section=acceptance)" in stderr


def test_case_4_malformed_markdown_link_emits_warning(
    runner, patched_cli, client, auth_headers, reviewer, project, tmp_path: Path
) -> None:
    """Case 4: 实施 log 段含不平衡 fragment → MALFORMED_MARKDOWN_LINK
    with detail; stderr line carries the fragment."""
    exp_id = _create_started_experiment(
        client, auth_headers, reviewer["headers"], project["id"]
    )
    result = _invoke_experiment_complete(
        runner, exp_id, "case4 malformed-link", _malformed_link_body(), tmp_path
    )
    assert result.exit_code == 0, f"stdout={result.stdout!r}, stderr={result.stderr!r}"

    payload = yaml.safe_load(result.stdout)
    warnings = payload["template_validation"]["warnings"]
    codes = [w["code"] for w in warnings]
    assert "MALFORMED_MARKDOWN_LINK" in codes
    malformed = [w for w in warnings if w["code"] == "MALFORMED_MARKDOWN_LINK"]
    assert malformed[0]["section"] == "实施 log"
    assert malformed[0]["detail"] is not None
    assert "[broken without close" in malformed[0]["detail"]
    assert payload["template_validation"]["valid"] is True
    assert payload["phase"] == "result_review"

    stderr = result.stderr or ""
    assert "[WARN] template: MALFORMED_MARKDOWN_LINK" in stderr
    assert "(section=实施 log)" in stderr
    assert "[broken without close" in stderr


# --- regression: warning emission is decoupled from result persistence ----


def test_complete_still_succeeds_with_template_warnings(
    runner, patched_cli, client, auth_headers, reviewer, project, tmp_path: Path
) -> None:
    """Soft validation invariant: warnings never block complete.

    The persisted experiment row transitions to ``result_review`` even
    when warnings are emitted; the host is expected to follow up.
    """
    exp_id = _create_started_experiment(
        client, auth_headers, reviewer["headers"], project["id"]
    )
    result = _invoke_experiment_complete(
        runner, exp_id, "soft complete test", _missing_summary_body(), tmp_path
    )
    assert result.exit_code == 0, f"stdout={result.stdout!r}, stderr={result.stderr!r}"

    payload = yaml.safe_load(result.stdout)
    assert payload["phase"] == "result_review"
    assert payload["template_validation"]["valid"] is True
    # And at least one warning was emitted
    assert payload["template_validation"]["warnings"]


def test_complete_does_not_emit_template_stderr_for_clean_body(
    runner, patched_cli, client, auth_headers, reviewer, project, tmp_path: Path
) -> None:
    """Regression: a clean body must not produce template warnings on stderr."""
    exp_id = _create_started_experiment(
        client, auth_headers, reviewer["headers"], project["id"]
    )
    result = _invoke_experiment_complete(
        runner, exp_id, "clean body", _COMPLETE_RESULT_BODY, tmp_path
    )
    assert result.exit_code == 0

    stderr = result.stderr or ""
    assert "[WARN] template:" not in stderr
    # No evidence parse error either (clean plan)
    assert "plan evidence_keys 解析失败" not in stderr


def test_complete_yaml_output_includes_template_validation(
    runner, patched_cli, client, auth_headers, reviewer, project, tmp_path: Path
) -> None:
    """Default ``--format yaml`` output is parseable YAML carrying the
    template_validation wrapper (I1.c). Mirrors 8ac93d4e I1.d regression
    that wrapper shape round-trips through yaml.safe_load.
    """
    exp_id = _create_started_experiment(
        client, auth_headers, reviewer["headers"], project["id"]
    )
    result = _invoke_experiment_complete(
        runner, exp_id, "yaml format", _missing_acceptance_body(), tmp_path
    )
    assert result.exit_code == 0, f"stdout={result.stdout!r}, stderr={result.stderr!r}"

    payload = yaml.safe_load(result.stdout)
    assert "template_validation" in payload
    assert isinstance(payload["template_validation"]["warnings"], list)
    assert any(
        w["code"] == "MISSING_TEMPLATE_SECTION" and w["section"] == "acceptance"
        for w in payload["template_validation"]["warnings"]
    )
