"""Unit tests for PRD v0.11 M52 — Skill distribution reliability.

Covers:
    - M52A: ``map-plugin.yaml`` manifest presence/validity for every
      bundled Skill, version-drift reporting on ``install`` skip,
      ``list --installed`` drift table, ``upgrade`` diff summary +
      overwrite, and the post-install self-check.
    - M52B: ``--runtime cursor|claude-code|codex|generic`` install
      targets.

All tests run in-process via CliRunner (no network, no subprocess).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from cli.commands.skill import (
    RUNTIME_TARGETS,
    _drift_label,
    _get_bundled_skills_dir,
    _list_skill_dirs,
    _read_version,
    _version_cmp,
    skill_app,
)

runner = CliRunner()

_REPO_ROOT = Path(__file__).resolve().parent.parent
_OLD_VERSION = "0.10.0"


def _write_manifest(skill_dir: Path, version: str) -> None:
    """Write a (possibly older) manifest into an installed Skill dir."""
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text("# old content\n", encoding="utf-8")
    (skill_dir / "map-plugin.yaml").write_text(
        yaml.safe_dump(
            {"version": version, "requires": "0.10", "runtime_targets": ["cursor"]},
            allow_unicode=True,
        ),
        encoding="utf-8",
    )


@pytest.fixture()
def human_format():
    """Guard against format leakage from --json tests in other modules."""
    from cli.main import _cli_options

    original = _cli_options.get("format")
    _cli_options["format"] = "yaml"
    try:
        yield
    finally:
        _cli_options["format"] = original


# ---------------------------------------------------------------------------
# M52A — manifest integrity
# ---------------------------------------------------------------------------


class TestPluginManifest:
    """Every bundled Skill ships a valid map-plugin.yaml."""

    def test_all_skills_have_manifest(self) -> None:
        skills_root = _get_bundled_skills_dir()
        for name in _list_skill_dirs():
            assert (skills_root / name / "map-plugin.yaml").is_file(), (
                f"Skill '{name}' is missing map-plugin.yaml (M52A)"
            )

    def test_manifest_fields_valid(self) -> None:
        skills_root = _get_bundled_skills_dir()
        for name in _list_skill_dirs():
            data = yaml.safe_load(
                (skills_root / name / "map-plugin.yaml").read_text(encoding="utf-8")
            )
            assert re.fullmatch(r"\d+\.\d+\.\d+", str(data["version"])), (
                f"{name}: version must be semver X.Y.Z"
            )
            assert str(data.get("requires", "")), f"{name}: requires is empty"
            targets = data.get("runtime_targets") or []
            unknown = set(targets) - set(RUNTIME_TARGETS)
            assert not unknown, f"{name}: unknown runtime_targets {unknown}"

    def test_cursor_mirror_manifests_in_sync(self) -> None:
        """The committed .cursor/skills mirror must carry the same version."""
        skills_root = _get_bundled_skills_dir()
        mirror_root = _REPO_ROOT / ".cursor" / "skills"
        for name in _list_skill_dirs():
            bundled = _read_version(skills_root / name)
            mirror = _read_version(mirror_root / name)
            assert bundled == mirror, (
                f"{name}: cli/skills v{bundled} != .cursor/skills v{mirror}"
            )


# ---------------------------------------------------------------------------
# M52A — version helper units
# ---------------------------------------------------------------------------


class TestVersionHelpers:
    def test_version_cmp_orders(self) -> None:
        assert _version_cmp("0.10.0", "0.11.0") < 0
        assert _version_cmp("0.11.0", "0.11.0") == 0
        assert _version_cmp("1.0", "0.99.9") > 0
        assert _version_cmp("0.11", "0.11.0") == 0

    def test_drift_label_variants(self) -> None:
        assert "update available" in _drift_label("0.10.0", "0.11.0")
        assert _drift_label("0.11.0", "0.11.0") == "up-to-date"
        assert "newer" in _drift_label("0.12.0", "0.11.0")
        assert "unknown" in _drift_label(None, "0.11.0")
        assert "unknown" in _drift_label("0.11.0", None)

    def test_read_version_missing_manifest(self, tmp_path: Path) -> None:
        (tmp_path / "SKILL.md").write_text("x")
        assert _read_version(tmp_path) is None


# ---------------------------------------------------------------------------
# M52A — install: version drift on skip + self-check
# ---------------------------------------------------------------------------


class TestInstallVersioning:
    def test_install_copies_manifest(self, tmp_path: Path, human_format) -> None:
        target = tmp_path / "skills"
        result = runner.invoke(skill_app, ["install", "--target", str(target)])
        assert result.exit_code == 0
        skills_root = _get_bundled_skills_dir()
        for name in _list_skill_dirs():
            assert (target / name / "map-plugin.yaml").is_file()
            assert _read_version(target / name) == _read_version(skills_root / name)

    def test_skip_reports_drift_and_upgrade_hint(
        self, tmp_path: Path, human_format
    ) -> None:
        target = tmp_path / "skills"
        _write_manifest(target / "topic-host", _OLD_VERSION)

        result = runner.invoke(
            skill_app, ["install", "--target", str(target), "--skill", "topic-host"]
        )
        assert result.exit_code == 0
        assert "Skip" in result.stdout
        # No silent skip: installed vs bundled version + recovery command
        assert _OLD_VERSION in result.stdout
        assert "map skill upgrade" in result.stdout
        assert (target / "topic-host" / "SKILL.md").read_text() == "# old content\n"

    def test_json_install_skip_fields(self, tmp_path: Path) -> None:
        import json

        from cli.main import app as main_app

        target = tmp_path / "skills"
        _write_manifest(target / "topic-host", _OLD_VERSION)

        result = runner.invoke(
            main_app,
            [
                "--json",
                "skill",
                "install",
                "--target",
                str(target),
                "--skill",
                "topic-host",
            ],
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        skipped = data["data"]["skipped"]
        assert len(skipped) == 1
        assert skipped[0]["installed_version"] == _OLD_VERSION
        assert skipped[0]["bundled_version"] is not None
        assert "upgrade" in skipped[0]["hint"]
        assert data["data"]["next_step"] == "map --persona host persona whoami"

    def test_post_install_self_check(self, tmp_path: Path, human_format) -> None:
        import os

        cwd = os.getcwd()
        os.chdir(tmp_path)  # no .map/config.yaml here
        try:
            result = runner.invoke(
                skill_app,
                ["install", "--target", str(tmp_path / "skills"), "-s", "topic-host"],
            )
            assert result.exit_code == 0
            assert "map --persona host persona whoami" in result.stdout
            # cwd has no .map/config.yaml → warn + troubleshooting anchor
            assert ".map/config.yaml" in result.stdout
            assert "bootstrap-troubleshooting.md" in result.stdout
        finally:
            os.chdir(cwd)


# ---------------------------------------------------------------------------
# M52B — runtime install targets
# ---------------------------------------------------------------------------


class TestRuntimeTargets:
    @pytest.mark.parametrize(
        ("runtime", "subdir"),
        [
            ("cursor", ".cursor/skills"),
            ("claude-code", ".claude/skills"),
            ("codex", ".codex/skills"),
            ("generic", "skills"),
        ],
    )
    def test_runtime_maps_to_target_dir(
        self, tmp_path: Path, runtime: str, subdir: str
    ) -> None:
        """--runtime installs into the mapped directory (run in tmp cwd)."""
        import os

        cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            result = runner.invoke(skill_app, ["install", "--runtime", runtime])
            assert result.exit_code == 0
            assert (tmp_path / subdir / "topic-host" / "SKILL.md").is_file()
        finally:
            os.chdir(cwd)

    def test_unknown_runtime_errors(self, tmp_path: Path) -> None:
        result = runner.invoke(skill_app, ["install", "--runtime", "vscode"])
        assert result.exit_code != 0
        assert "unknown runtime" in result.output

    def test_explicit_target_overrides_runtime(self, tmp_path: Path) -> None:
        """--target wins over --runtime (documented precedence)."""
        result = runner.invoke(
            skill_app,
            [
                "install",
                "--runtime",
                "claude-code",
                "--target",
                str(tmp_path / "custom"),
            ],
        )
        assert result.exit_code == 0
        assert (tmp_path / "custom" / "topic-host").is_dir()
        assert not (tmp_path / ".claude").exists()


# ---------------------------------------------------------------------------
# M52A — list --installed
# ---------------------------------------------------------------------------


class TestListInstalled:
    def test_shows_drift_and_missing(self, tmp_path: Path, human_format) -> None:
        target = tmp_path / "skills"
        _write_manifest(target / "topic-host", _OLD_VERSION)

        result = runner.invoke(
            skill_app,
            ["list", "--installed", "--target", str(target)],
        )
        assert result.exit_code == 0
        assert f"update available ({_OLD_VERSION} →" in result.stdout
        assert "topic-host" in result.stdout
        # topic-participant exists only bundled
        assert "not installed" in result.stdout

    def test_up_to_date_after_install(self, tmp_path: Path, human_format) -> None:
        target = tmp_path / "skills"
        runner.invoke(skill_app, ["install", "--target", str(target)])
        result = runner.invoke(
            skill_app, ["list", "--installed", "--target", str(target)]
        )
        assert result.exit_code == 0
        assert "up-to-date" in result.stdout

    def test_json_fields(self, tmp_path: Path) -> None:
        import json

        from cli.main import app as main_app

        target = tmp_path / "skills"
        _write_manifest(target / "topic-host", _OLD_VERSION)

        result = runner.invoke(
            main_app,
            ["--json", "skill", "list", "--installed", "--target", str(target)],
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        by_name = {s["name"]: s for s in data["data"]["skills"]}
        assert by_name["topic-host"]["installed_version"] == _OLD_VERSION
        assert "update available" in by_name["topic-host"]["drift"]
        assert by_name["topic-participant"]["installed"] is False


# ---------------------------------------------------------------------------
# M52A — upgrade
# ---------------------------------------------------------------------------


class TestUpgrade:
    def test_upgrade_shows_diff_then_overwrites(
        self, tmp_path: Path, human_format
    ) -> None:
        target = tmp_path / "skills"
        _write_manifest(target / "topic-host", _OLD_VERSION)

        result = runner.invoke(
            skill_app,
            ["upgrade", "--target", str(target), "--skill", "topic-host"],
        )
        assert result.exit_code == 0
        # Diff summary before overwrite: file counts + line counts
        assert "Diff:" in result.stdout
        assert re.search(r"~\d+ file\(s\) changed", result.stdout)
        assert re.search(r"\+\d+/-\d+ lines", result.stdout)
        assert "Upgraded:" in result.stdout

        # Old content replaced with the bundled Skill
        assert (target / "topic-host" / "SKILL.md").read_text() != "# old content\n"
        assert _read_version(target / "topic-host") == _read_version(
            _get_bundled_skills_dir() / "topic-host"
        )

    def test_upgrade_no_diff_skips(self, tmp_path: Path, human_format) -> None:
        target = tmp_path / "skills"
        runner.invoke(skill_app, ["install", "--target", str(target)])

        result = runner.invoke(
            skill_app, ["upgrade", "--target", str(target)]
        )
        assert result.exit_code == 0
        assert "Up-to-date" in result.stdout
        assert "Upgraded" not in result.stdout

    def test_upgrade_force_skips_diff(self, tmp_path: Path, human_format) -> None:
        target = tmp_path / "skills"
        _write_manifest(target / "topic-host", _OLD_VERSION)

        result = runner.invoke(
            skill_app,
            ["upgrade", "--target", str(target), "--skill", "topic-host", "--force"],
        )
        assert result.exit_code == 0
        assert "Diff:" not in result.stdout
        assert "Upgraded:" in result.stdout
        assert _read_version(target / "topic-host") != _OLD_VERSION

    def test_upgrade_not_installed_hint(self, tmp_path: Path, human_format) -> None:
        target = tmp_path / "skills"
        target.mkdir()  # target exists, but no Skills installed yet
        result = runner.invoke(skill_app, ["upgrade", "--target", str(target)])
        assert result.exit_code == 0
        assert "Not installed" in result.stdout
        assert "map skill install" in result.stdout

    def test_upgrade_missing_target_dir_errors(self, tmp_path: Path) -> None:
        result = runner.invoke(skill_app, ["upgrade", "--target", str(tmp_path / "nope")])
        assert result.exit_code == 1

    def test_json_upgrade(self, tmp_path: Path) -> None:
        import json

        from cli.main import app as main_app

        target = tmp_path / "skills"
        _write_manifest(target / "topic-host", _OLD_VERSION)

        result = runner.invoke(
            main_app,
            [
                "--json",
                "skill",
                "upgrade",
                "--target",
                str(target),
                "--skill",
                "topic-host",
            ],
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["ok"] is True
        assert data["data"]["upgraded_count"] == 1
        assert data["data"]["upgraded"] == ["topic-host"]
