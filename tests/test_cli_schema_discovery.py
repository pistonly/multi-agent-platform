"""cli-ux PR1: --schema flag + error message schema path hint.

These tests guard the schema-discovery contract for two CLI commands:

* ``map experiment accept-result --schema`` / ``reject-result --schema``
  prints a copy-paste-ready review verdict YAML template.
* ``map experiment complete --schema`` prints a copy-paste-ready metadata
  YAML template.
* Both error paths (``_load_review_verdict_file`` /
  ``_load_complete_metadata``) append a schema-location hint pointing
  at the SDK class and the canonical docs page.
* The templates round-trip: a file written from ``--schema`` output
  parses cleanly through the actual Pydantic validator.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    """Invoke ``map`` CLI via python -m so PATH-independent."""
    return subprocess.run(
        [sys.executable, "-m", "cli.main", *args],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=30,
    )


# --- --schema output --------------------------------------------------------


def test_experiment_complete_schema_flag_prints_template() -> None:
    proc = _run_cli("experiment", "complete", "--schema")
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    # Template mentions every accepted key so user discovers them all.
    for key in ("api_health", "alembic_current", "pytest_summary", "smoke"):
        assert key in out, f"--schema output missing accepted key: {key}"
    # Hint points at the canonical docs page.
    assert "docs/cli-schemas.md#experiment-complete-metadata" in out


def test_experiment_accept_result_schema_flag_prints_template() -> None:
    proc = _run_cli("experiment", "accept-result", "--schema")
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    # Template has the three top-level fields.
    assert "review_id:" in out
    assert "verdicts:" in out
    assert "invariants:" in out
    # Hint mentions SDK class + docs page.
    assert "ReviewVerdictFile" in out
    assert "docs/cli-schemas.md#review-verdict-file" in out
    # Verdict enum values listed (so user doesn't have to grep SDK).
    for verdict in ("passed", "failed", "waived"):
        assert verdict in out


def test_experiment_reject_result_schema_flag_prints_template() -> None:
    """reject-result takes the same --review-verdict-file schema as accept-result."""
    proc = _run_cli("experiment", "reject-result", "--schema")
    assert proc.returncode == 0, proc.stderr
    assert "review_id:" in proc.stdout


# --- error message hints ---------------------------------------------------


def test_invalid_verdict_file_error_mentions_schema_path(tmp_path: Path) -> None:
    """Bad YAML → clean error (exit 2) with schema-location hint appended."""
    bad = tmp_path / "bad.yaml"
    bad.write_text("verdicts: not-a-list\n", encoding="utf-8")
    proc = _run_cli(
        "experiment",
        "accept-result",
        "--id",
        "00000000-0000-0000-0000-000000000000",
        "--summary",
        "x",
        "--file",
        str(tmp_path / "log.md"),
        "--review-verdict-file",
        str(bad),
    )
    assert proc.returncode == 2, proc.stderr
    err = proc.stderr
    assert "schema:" in err, f"error should hint at schema path, got: {err}"
    assert "ReviewVerdictFile" in err


# --- round-trip -------------------------------------------------------------


def test_complete_schema_template_round_trips() -> None:
    """A metadata file written from --schema output passes
    ``metadata_has_completion_evidence`` (which is what blocks non-deployment
    experiments from completing).
    """
    proc = _run_cli("experiment", "complete", "--schema")
    assert proc.returncode == 0
    # Strip the comment-only lines so yaml.safe_load doesn't choke.
    raw_lines = [
        ln for ln in proc.stdout.splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    parsed = yaml.safe_load("\n".join(raw_lines))
    assert isinstance(parsed, dict)
    # Template provides api_health + pytest_summary + smoke — at least one
    # accepted evidence key must be present.
    from map_sdk.evidence import EVIDENCE_METADATA_KEYS, metadata_has_completion_evidence

    assert any(k in parsed for k in EVIDENCE_METADATA_KEYS)
    assert metadata_has_completion_evidence(parsed)


def test_review_verdict_schema_template_round_trips() -> None:
    """The verdict template (after replacing placeholder UUIDs) parses
    cleanly through ``ReviewVerdictFile.model_validate``.
    """
    proc = _run_cli("experiment", "accept-result", "--schema")
    assert proc.returncode == 0
    # Substitute placeholder UUIDs so Pydantic accepts it as UUID, not str.
    raw = proc.stdout.replace("00000000-0000-0000-0000-000000000000", str(__import__("uuid").uuid4()))
    raw_lines = [
        ln for ln in raw.splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    parsed = yaml.safe_load("\n".join(raw_lines))
    assert isinstance(parsed, dict)
    assert "review_id" in parsed
    # ``verdicts:`` and ``invariants:`` may be empty after stripping comments;
    # instantiate the model anyway — Pydantic accepts empty lists (default_factory).
    from map_types.schemas import ReviewVerdictFile

    ReviewVerdictFile.model_validate(parsed)


# --- docs link sanity -------------------------------------------------------


def test_cli_schemas_doc_exists_and_links_back() -> None:
    """docs/cli-schemas.md exists and lists both schemas."""
    doc = REPO_ROOT / "docs" / "cli-schemas.md"
    assert doc.is_file(), f"missing canonical schema doc: {doc}"
    body = doc.read_text(encoding="utf-8")
    assert "review-verdict-file" in body
    assert "experiment-complete-metadata" in body
    assert "ReviewVerdictFile" in body
