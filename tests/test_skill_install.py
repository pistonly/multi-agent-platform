"""Unit tests for cli/commands/skill.py — Skill install commands.

Tests verify that bundled Skills are discoverable and install correctly
to a target directory, including selective install and force overwrite.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cli.commands.skill import skill_app, _get_bundled_skills_dir, _list_skill_dirs


runner = CliRunner()


class TestBundledSkills:
    """Tests for Skill discovery from the package."""

    def test_skills_dir_exists(self):
        """The bundled skills directory should exist inside the package."""
        skills_dir = _get_bundled_skills_dir()
        assert skills_dir.is_dir(), f"Skills directory not found at {skills_dir}"

    def test_all_skills_have_skill_md(self):
        """Every Skill directory should contain a SKILL.md file."""
        skills_root = _get_bundled_skills_dir()
        for name in _list_skill_dirs():
            assert (skills_root / name / "SKILL.md").exists(), (
                f"Skill '{name}' is missing SKILL.md"
            )

    def test_expected_skills_present(self):
        """The core MAP Skills should all be bundled."""
        names = set(_list_skill_dirs())
        expected = {
            "map-project-collab",
            "topic-host",
            "topic-participant",
            "experiment-host",
            "experiment-reviewer",
        }
        assert expected.issubset(names), f"Missing skills: {expected - names}"


class TestSkillList:
    """Tests for `map skill list` command."""

    def test_lists_all_skills(self):
        """The list command should output all bundled Skill names."""
        result = runner.invoke(skill_app, ["list"])
        assert result.exit_code == 0
        for name in _list_skill_dirs():
            assert name in result.stdout


class TestSkillInstall:
    """Tests for `map skill install` command."""

    def test_install_all(self, tmp_path):
        """Installing all Skills should create all directories."""
        target = tmp_path / "skills"
        result = runner.invoke(skill_app, ["install", "--target", str(target)])
        assert result.exit_code == 0
        assert target.is_dir()
        # Each Skill should have its own directory
        for name in _list_skill_dirs():
            assert (target / name / "SKILL.md").exists()

    def test_install_specific_skill(self, tmp_path):
        """Installing a single Skill should only create that directory."""
        target = tmp_path / "skills"
        result = runner.invoke(
            skill_app,
            ["install", "--target", str(target), "--skill", "topic-host"],
        )
        assert result.exit_code == 0
        assert (target / "topic-host" / "SKILL.md").exists()
        # Other skills should NOT be installed
        assert not (target / "topic-participant").exists()

    def test_install_multiple_specific(self, tmp_path):
        """Installing multiple named Skills with repeated --skill flags."""
        target = tmp_path / "skills"
        result = runner.invoke(
            skill_app,
            [
                "install",
                "--target", str(target),
                "--skill", "topic-host",
                "--skill", "topic-participant",
            ],
        )
        assert result.exit_code == 0
        assert (target / "topic-host").exists()
        assert (target / "topic-participant").exists()
        assert not (target / "experiment-host").exists()

    def test_install_unknown_skill_errors(self, tmp_path):
        """Installing a non-existent Skill should error."""
        target = tmp_path / "skills"
        result = runner.invoke(
            skill_app,
            ["install", "--target", str(target), "--skill", "nonexistent"],
        )
        assert result.exit_code == 1
        assert "unknown Skill" in (result.stdout + result.output)

    def test_skip_existing_without_force(self, tmp_path):
        """Existing Skill directories should be skipped without --force."""
        target = tmp_path / "skills"
        # Pre-create a directory
        (target / "topic-host").mkdir(parents=True)
        (target / "topic-host" / "SKILL.md").write_text("# Old content")

        result = runner.invoke(skill_app, ["install", "--target", str(target)])
        assert result.exit_code == 0
        assert "Skip" in result.stdout

        # Content should NOT be overwritten
        content = (target / "topic-host" / "SKILL.md").read_text()
        assert content == "# Old content"

    def test_force_overwrites_existing(self, tmp_path):
        """With --force, existing directories should be overwritten."""
        target = tmp_path / "skills"
        # Pre-create a directory with old content
        (target / "topic-host").mkdir(parents=True)
        (target / "topic-host" / "SKILL.md").write_text("# Old content")

        result = runner.invoke(
            skill_app,
            ["install", "--target", str(target), "--force", "--skill", "topic-host"],
        )
        assert result.exit_code == 0
        assert "Installed" in result.stdout

        # Content should be overwritten
        content = (target / "topic-host" / "SKILL.md").read_text()
        assert content != "# Old content"
        assert "topic-host" in content.lower() or "MAP" in content

    def test_install_preserves_subdirectories(self, tmp_path):
        """Skills with subdirectories (scripts/, agents/) should be copied fully."""
        target = tmp_path / "skills"
        result = runner.invoke(skill_app, ["install", "--target", str(target)])
        assert result.exit_code == 0

        # map-project-collab has a scripts/ subdirectory
        assert (target / "map-project-collab" / "scripts" / "map-bootstrap.sh").exists()

    def test_install_creates_target_dir(self, tmp_path):
        """Target directory should be created if it doesn't exist."""
        target = tmp_path / "deep" / "nested" / "skills"
        result = runner.invoke(skill_app, ["install", "--target", str(target)])
        assert result.exit_code == 0
        assert target.is_dir()
