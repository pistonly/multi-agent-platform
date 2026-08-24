"""3b7c2b44 A6 P3-1 — ``map version``（CLI 版本 + 关键命令集 skills 对照范围）。

要求：``map version --json`` 输出 CLI 版本（与 ``map --version`` 同源）+ 关键
命令集（fs/topic/bootstrap/review）依赖的 bundled skills 版本对照范围；不做
酸断言（开发期 version 未 bump 是常态）、不建全量漂移表。
"""
from __future__ import annotations

import json

from typer.testing import CliRunner

from cli.commands.version import version_app

runner = CliRunner()


def test_version_human_prints_semver() -> None:
    result = runner.invoke(version_app, [])
    assert result.exit_code == 0, result.output
    first = result.output.strip().splitlines()[0]
    assert first.startswith("map ")


def test_version_json_has_cli_and_scope_commands() -> None:
    result = runner.invoke(version_app, ["--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["cli"]["version"]
    scope_commands = set(payload["scope"]["commands"])
    assert {"fs", "topic", "bootstrap", "review"} <= scope_commands


def test_version_json_skills_have_bundled_version() -> None:
    result = runner.invoke(version_app, ["--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    skills = payload["scope"]["skills"]
    assert skills
    for entry in skills.values():
        assert entry["bundled_version"]


def test_version_matches_global_flag_version() -> None:
    """``map version`` 与 ``map --version`` 同源（map_sdk.__version__）。"""
    import cli.main as cli_main
    from cli.main import app as main_app

    v1 = runner.invoke(version_app, []).output.strip().splitlines()[0]
    v2 = runner.invoke(main_app, ["--version"]).output.strip()
    assert v1 == v2 == f"map {cli_main._cli_version()}"
