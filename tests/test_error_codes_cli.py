"""CLI ``map docs error-codes`` (8a8822b5 (d) acceptance).

Pins plan v2 (d) acceptance:

* Default invocation lists every error code in
  ``docs/error-codes/index.json`` with code / category / http /
  title / owner_experiment columns.
* ``--search <kw>`` filters via case-insensitive substring match against
  the union of code / category / title / description / hint / docs_url /
  owner_experiment / since_version fields.
* Multiple ``--search`` values are AND-combined; comma-separated values
  within a single ``--search`` are split and AND-combined.
* Missing-keyword search returns zero rows with a clean ``# matched: 0/N``
  header (no table, exit 0).
* ``--json`` emits the raw ``index.json`` payload as YAML (machine-readable).
* A corrupt or missing index file exits 2 with a friendly error
  (no traceback).

No MAP client / API call required; ``docs`` sub-app is local-only.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from cli.commands.docs import (
    _ERROR_CODES_SEARCH_FIELDS,
    _match_error_code,
    _search_keywords_to_list,
)
from cli.main import app


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# ---- helpers ---------------------------------------------------------------


def _load_repo_index() -> dict:
    repo_root = Path(__file__).resolve().parents[1]
    return json.loads((repo_root / "docs/error-codes/index.json").read_text())


def _all_codes_from_default_output(output: str) -> list[str]:
    """Extract the `code` column from the default CLI table."""
    rows = []
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("code "):
            continue
        if set(stripped) <= set("-+|"):
            continue
        rows.append(stripped.split(" | ")[0].strip())
    return rows


# ---- tests -----------------------------------------------------------------


def test_docs_error_codes_lists_all_codes(runner):
    """Default invocation prints every code from the repo index."""
    result = runner.invoke(app, ["docs", "error-codes"])
    assert result.exit_code == 0, result.stdout
    payload = _load_repo_index()
    codes = [entry["code"] for entry in payload["codes"]]
    for code in codes:
        assert code in result.stdout, f"missing code {code} in output:\n{result.stdout}"
    rendered = _all_codes_from_default_output(result.stdout)
    assert sorted(rendered) == sorted(codes)


def test_docs_error_codes_header_metadata(runner):
    """The default table includes schema_version / generated_at / owner_experiment."""
    result = runner.invoke(app, ["docs", "error-codes"])
    assert result.exit_code == 0, result.stdout
    payload = _load_repo_index()
    assert f"# schema_version: {payload['schema_version']}" in result.stdout
    assert f"# generated_at: {payload['generated_at']}" in result.stdout
    assert f"# owner_experiment: {payload['owner_experiment']}" in result.stdout
    assert f"# matched: {len(payload['codes'])}/{len(payload['codes'])}" in result.stdout


def test_docs_error_codes_search_single_keyword_hit(runner):
    """A single keyword filters down to matching rows (case-insensitive)."""
    result = runner.invoke(app, ["docs", "error-codes", "--search", "review"])
    assert result.exit_code == 0, result.stdout
    payload = _load_repo_index()
    expected_codes = [
        entry["code"]
        for entry in payload["codes"]
        if "review" in (entry.get("category") or "").lower()
        or "review" in (entry.get("title") or "").lower()
        or any(
            "review" in str(entry.get(field) or "").lower()
            for field in ("code", "description", "hint", "docs_url",
                          "owner_experiment", "since_version")
        )
    ]
    rendered = _all_codes_from_default_output(result.stdout)
    assert sorted(rendered) == sorted(expected_codes)
    assert all("REVIEW" in code or "review" in code for code in expected_codes)


def test_docs_error_codes_search_no_hit(runner):
    """A keyword that matches nothing produces a clean header and no table rows."""
    result = runner.invoke(app, ["docs", "error-codes", "--search", "zzz_nonexistent_xyz"])
    assert result.exit_code == 0, result.stdout
    assert "# matched: 0/" in result.stdout
    # No table data rows.
    assert "REVIEW_ALREADY_ARCHIVED" not in result.stdout
    assert "state_machine_error" not in result.stdout


def test_docs_error_codes_search_multi_keyword_and_repeat(runner):
    """Multiple --search flags are AND-combined (intersection)."""
    result = runner.invoke(
        app,
        [
            "docs",
            "error-codes",
            "--search",
            "review",
            "--search",
            "18f1d8f6",
        ],
    )
    assert result.exit_code == 0, result.stdout
    payload = _load_repo_index()
    rendered = _all_codes_from_default_output(result.stdout)

    def _haystack(entry: dict) -> str:
        parts = []
        for field in _ERROR_CODES_SEARCH_FIELDS:
            v = entry.get(field)
            if v is not None:
                parts.append(str(v))
        return "\n".join(parts).lower()

    expected = [
        entry["code"]
        for entry in payload["codes"]
        if "review" in _haystack(entry) and "18f1d8f6" in _haystack(entry)
    ]
    assert sorted(rendered) == sorted(expected)


def test_docs_error_codes_search_multi_keyword_and_comma_split(runner):
    """Comma-separated values within one --search flag are AND-combined too."""
    result = runner.invoke(
        app,
        ["docs", "error-codes", "--search", "review,reject"],
    )
    assert result.exit_code == 0, result.stdout
    payload = _load_repo_index()
    rendered = _all_codes_from_default_output(result.stdout)

    def _haystack(entry: dict) -> str:
        parts = []
        for field in _ERROR_CODES_SEARCH_FIELDS:
            v = entry.get(field)
            if v is not None:
                parts.append(str(v))
        return "\n".join(parts).lower()

    expected = [
        entry["code"]
        for entry in payload["codes"]
        if "review" in _haystack(entry) and "reject" in _haystack(entry)
    ]
    assert sorted(rendered) == sorted(expected)
    # REVIEW_REJECT_RESULT_MISUSE contains both keywords; REVIEW_ALREADY_ARCHIVED does not.
    assert "REVIEW_REJECT_RESULT_MISUSE" in rendered


def test_docs_error_codes_json_output_is_machine_readable(runner):
    """--json emits the full index payload as YAML (parseable round-trip)."""
    result = runner.invoke(app, ["docs", "error-codes", "--json"])
    assert result.exit_code == 0, result.stdout
    parsed = yaml.safe_load(result.stdout)
    assert isinstance(parsed, dict)
    assert "codes" in parsed and isinstance(parsed["codes"], list)
    payload = _load_repo_index()
    assert len(parsed["codes"]) == len(payload["codes"])
    rendered_codes = [entry["code"] for entry in parsed["codes"]]
    assert sorted(rendered_codes) == sorted(entry["code"] for entry in payload["codes"])


def test_docs_error_codes_json_with_search_filters(runner):
    """--json + --search returns only matched entries (filtering at JSON level)."""
    result = runner.invoke(app, ["docs", "error-codes", "--json", "--search", "review"])
    assert result.exit_code == 0, result.stdout
    parsed = yaml.safe_load(result.stdout)
    rendered_codes = [entry["code"] for entry in parsed["codes"]]
    assert rendered_codes  # at least one match
    for entry in parsed["codes"]:
        haystack = "\n".join(
            str(entry.get(field) or "")
            for field in _ERROR_CODES_SEARCH_FIELDS
        ).lower()
        assert "review" in haystack


def test_docs_error_codes_missing_index_exits_2(runner, tmp_path, monkeypatch):
    """When the index file is missing, exit 2 + friendly stderr (no traceback)."""
    # Point project_root to a tmp dir without docs/error-codes/index.json.
    fake_root = tmp_path / "no_docs_repo"
    fake_root.mkdir()
    result = runner.invoke(
        app,
        ["docs", "error-codes", "--project-root", str(fake_root)],
    )
    assert result.exit_code == 2
    assert "Error: error codes index not found" in (result.stderr or "")
    assert "Traceback" not in (result.stderr or "")


def test_docs_error_codes_corrupt_index_exits_2(runner, tmp_path):
    """A malformed YAML/JSON index exits 2 (no traceback leaked)."""
    fake_root = tmp_path / "bad_repo"
    docs_dir = fake_root / "docs" / "error-codes"
    docs_dir.mkdir(parents=True)
    (docs_dir / "index.json").write_text("{ this is not valid json", encoding="utf-8")
    result = runner.invoke(
        app,
        ["docs", "error-codes", "--project-root", str(fake_root)],
    )
    assert result.exit_code == 2
    assert "Traceback" not in (result.stderr or "")


def test_docs_error_codes_index_without_codes_key_exits_2(runner, tmp_path):
    """An index missing the required 'codes' list exits 2."""
    fake_root = tmp_path / "shape_repo"
    docs_dir = fake_root / "docs" / "error-codes"
    docs_dir.mkdir(parents=True)
    (docs_dir / "index.json").write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
    result = runner.invoke(
        app,
        ["docs", "error-codes", "--project-root", str(fake_root)],
    )
    assert result.exit_code == 2
    assert "'codes' list" in (result.stderr or "")


def test_docs_error_codes_normalizes_search_dedupe_and_strip(runner):
    """Whitespace / case / dedupe is normalized before matching."""
    result = runner.invoke(
        app,
        ["docs", "error-codes", "--search", "  REVIEW  ", "--search", "review"],
    )
    assert result.exit_code == 0, result.stdout
    # Header should show only the deduped keyword (lowercased-trimmed form).
    assert "REVIEW" in result.stdout
    # No double-print of the same keyword in the search header.
    search_line = next(
        line for line in result.stdout.splitlines() if line.startswith("# search:")
    )
    assert search_line.count("review") == 1


def test_docs_error_codes_module_helpers_exposed():
    """Pure helpers are importable + produce stable results for direct callers."""
    # Pure matcher: empty keywords matches everything.
    assert _match_error_code({"code": "x"}, []) is True
    # Substring across any of the configured fields (case-insensitive).
    assert _match_error_code(
        {"code": "REVIEW_X", "category": "review", "description": "longer text"},
        ["review"],
    ) is True
    assert _match_error_code(
        {"code": "REVIEW_X", "category": "review", "description": "longer text"},
        ["REVIEW", "nonexistent"],
    ) is False
    # AND semantics: both keywords must hit.
    assert _match_error_code(
        {"code": "A_B", "description": "alpha beta"},
        ["alpha", "beta"],
    ) is True
    assert _match_error_code(
        {"code": "A_B", "description": "alpha"},
        ["alpha", "beta"],
    ) is False


def test_docs_error_codes_search_keywords_to_list_dedupes_and_preserves_order():
    """Comma split + strip + dedupe preserves first-seen order."""
    out = _search_keywords_to_list(
        ["REVIEW", "review,state", "  state_machine  ", "", "review"]
    )
    assert out == ["REVIEW", "review", "state", "state_machine"]


def test_docs_error_codes_search_matches_across_all_indexed_fields(runner):
    """A keyword that lives in hint / docs_url / description is matched."""
    payload = _load_repo_index()
    # Pick the first code's hint to build a keyword guaranteed to appear.
    hint = payload["codes"][0]["hint"]
    needle = hint.split()[0]  # first word of hint
    result = runner.invoke(
        app,
        ["docs", "error-codes", "--search", needle],
    )
    assert result.exit_code == 0, result.stdout
    rendered = _all_codes_from_default_output(result.stdout)
    assert payload["codes"][0]["code"] in rendered
