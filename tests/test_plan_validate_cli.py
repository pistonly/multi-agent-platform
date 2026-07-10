"""a764abf6 I1.(c) — ``map experiment plan validate`` CLI tests.

Covers the 3-bucket acceptance from plan (c):

1. 完整 frontmatter → exit 0，warnings=[]
2. 缺 title → exit 0（默认非 strict），warning 含 PLAN_MARKER_MISSING_FIELD(title)
3. ``--strict`` + 缺 title → exit 1

We invoke ``CliRunner`` against ``cli.main.app`` (``experiment plan validate``)
to exercise the local lint path (no MAP client involved).
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cli.main import app as cli_app

_FULL_PLAN = """---
title: plan lint smoke test
acceptance:
  - 完整 frontmatter 不告警
evidence_keys:
  - pytest_summary
dependencies:
  - 0d5717d6-d672-46be-bc35-90ee08391f05
---

# Plan
"""

_MISSING_TITLE_PLAN = """---
acceptance:
  - 缺 title 应当告警
evidence_keys:
  - pytest_summary
dependencies:
  - 0d5717d6-d672-46be-bc35-90ee08391f05
---

# Plan
"""


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def tmp_plan_dir(tmp_path: Path) -> Path:
    return tmp_path


def _write_plan(directory: Path, content: str, *, suffix: str = ".md") -> Path:
    p = directory / f"plan-{uuid.uuid4().hex[:6]}{suffix}"
    p.write_text(content, encoding="utf-8")
    return p


def test_case_1_full_frontmatter_exits_zero(runner: CliRunner, tmp_plan_dir: Path):
    """完整 frontmatter → 0 warnings, exit 0."""
    plan_path = _write_plan(tmp_plan_dir, _FULL_PLAN)
    result = runner.invoke(cli_app, ["experiment", "plan", "validate", "--plan-file", str(plan_path)])
    assert result.exit_code == 0, result.stdout + (result.stderr or "")
    assert "warnings=0" in result.stdout
    assert "PLAN_MARKER_MISSING_FIELD" not in result.stdout


def test_case_2_missing_title_emits_warning_exit_zero(runner: CliRunner, tmp_plan_dir: Path):
    """缺 title → warning 但默认 exit 0（soft）。"""
    plan_path = _write_plan(tmp_plan_dir, _MISSING_TITLE_PLAN)
    result = runner.invoke(
        cli_app,
        ["experiment", "plan", "validate", "--plan-file", str(plan_path)],
    )
    assert result.exit_code == 0, result.stdout + (result.stderr or "")
    assert "PLAN_MARKER_MISSING_FIELD" in result.stdout
    assert "title" in result.stdout


def test_case_3_strict_with_missing_title_exits_one(runner: CliRunner, tmp_plan_dir: Path):
    """``--strict`` + 缺 title → exit 1。"""
    plan_path = _write_plan(tmp_plan_dir, _MISSING_TITLE_PLAN)
    result = runner.invoke(
        cli_app,
        [
            "experiment",
            "plan",
            "validate",
            "--plan-file",
            str(plan_path),
            "--strict",
        ],
    )
    assert result.exit_code == 1, result.stdout + (result.stderr or "")


def test_case_3b_strict_with_full_frontmatter_exits_zero(runner: CliRunner, tmp_plan_dir: Path):
    """``--strict`` + 完整 frontmatter → exit 0。"""
    plan_path = _write_plan(tmp_plan_dir, _FULL_PLAN)
    result = runner.invoke(
        cli_app,
        [
            "experiment",
            "plan",
            "validate",
            "--plan-file",
            str(plan_path),
            "--strict",
        ],
    )
    assert result.exit_code == 0, result.stdout + (result.stderr or "")


def test_case_4_no_frontmatter_emits_front_matter_missing(runner: CliRunner, tmp_plan_dir: Path):
    """无 frontmatter → FRONT_MATTER_MISSING + 4 个 MISSING_FIELD。"""
    body = "# Plan without frontmatter\n\nsome prose"
    plan_path = _write_plan(tmp_plan_dir, body)
    result = runner.invoke(
        cli_app,
        ["experiment", "plan", "validate", "--plan-file", str(plan_path)],
    )
    assert result.exit_code == 0
    assert "PLAN_MARKER_FRONT_MATTER_MISSING" in result.stdout
    # 4 个必填字段均缺失
    assert result.stdout.count("PLAN_MARKER_MISSING_FIELD") >= 4


def test_case_5_missing_plan_file_exits_two(runner: CliRunner, tmp_plan_dir: Path):
    """--plan-file 指向不存在路径 → exit 2 + 友好提示，不泄漏 traceback。"""
    result = runner.invoke(
        cli_app,
        [
            "experiment",
            "plan",
            "validate",
            "--plan-file",
            str(tmp_plan_dir / "does-not-exist.md"),
        ],
    )
    assert result.exit_code == 2
    combined = (result.stdout or "") + (result.stderr or "")
    assert "plan file not found" in combined
    assert "Traceback" not in combined


# --- experiment_create 前置 lint 门禁 -----------------------------


def test_case_6_experiment_create_blocks_on_missing_title(
    runner: CliRunner, tmp_plan_dir: Path
):
    """`experiment create` 缺 title → exit 2 + lint hint，不调 API。"""
    plan_path = _write_plan(tmp_plan_dir, _MISSING_TITLE_PLAN)
    result = runner.invoke(
        cli_app,
        [
            "experiment",
            "create",
            "--title",
            "x",
            "--plan-file",
            str(plan_path),
        ],
    )
    assert result.exit_code == 2
    combined = (result.stdout or "") + (result.stderr or "")
    assert "frontmatter lint failed" in combined
    assert "--force-lint-bypass" in combined


def test_case_7_experiment_create_blocks_on_no_frontmatter(
    runner: CliRunner, tmp_plan_dir: Path
):
    """无 frontmatter → lint 门禁 exit 2。"""
    plan_path = _write_plan(tmp_plan_dir, "# only prose")
    result = runner.invoke(
        cli_app,
        [
            "experiment",
            "create",
            "--title",
            "x",
            "--plan-file",
            str(plan_path),
        ],
    )
    assert result.exit_code == 2
    combined = (result.stdout or "") + (result.stderr or "")
    assert "lint failed" in combined


def test_case_8_experiment_create_force_lint_bypass_passes_local_check(
    runner: CliRunner, tmp_plan_dir: Path
):
    """``--force-lint-bypass`` 让 lint 失败的 plan 也通过本地门禁
    （仍会调 API；此处无 client → 会因网络失败但**不**因 lint 失败）。"""
    plan_path = _write_plan(tmp_plan_dir, _MISSING_TITLE_PLAN)
    result = runner.invoke(
        cli_app,
        [
            "experiment",
            "create",
            "--title",
            "x",
            "--plan-file",
            str(plan_path),
            "--force-lint-bypass",
        ],
    )
    # local lint 通过；下一步是 _run(action) → _client_ctx → HTTP，
    # 失败但 exit code != 2（即不是 lint 错误）
    assert result.exit_code != 2
