"""Unit tests for cli/commands/skill.py — Skill install commands.

Tests verify that bundled Skills are discoverable and install correctly
to a target directory, including selective install and force overwrite.
"""
from __future__ import annotations

import re
from pathlib import Path

from typer.testing import CliRunner

from cli.commands.skill import _get_bundled_skills_dir, _list_skill_dirs, skill_app

runner = CliRunner()

REPO_ROOT = Path(__file__).resolve().parents[1]


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
            "experiment-executor",
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

    def test_install_preserves_references_dirs(self, tmp_path):
        """Skills with references/ subdirectories should be copied fully."""
        target = tmp_path / "skills"
        result = runner.invoke(skill_app, ["install", "--target", str(target)])
        assert result.exit_code == 0

        # map-project-collab should have references/ with .md files
        refs = target / "map-project-collab" / "references"
        assert refs.is_dir()
        assert (refs / "waker-mode.md").exists()
        assert (refs / "bootstrap-troubleshooting.md").exists()

        # topic-host should have references/
        assert (target / "topic-host" / "references" / "experiment-gate-rubric.md").exists()

        # experiment-host should have references/
        assert (target / "experiment-host" / "references" / "lifecycle-transitions.md").exists()

        # experiment-reviewer should have references/
        assert (target / "experiment-reviewer" / "references" / "review-format-guide.md").exists()

    def test_install_creates_target_dir(self, tmp_path):
        """Target directory should be created if it doesn't exist."""
        target = tmp_path / "deep" / "nested" / "skills"
        result = runner.invoke(skill_app, ["install", "--target", str(target)])
        assert result.exit_code == 0
        assert target.is_dir()


class TestSkillJsonOutput:
    """Tests for --format json / --json output on skill commands."""

    def test_json_list(self):
        """--json skill list should output {ok: true, data: {skills: [...]}}."""
        from cli.main import app as main_app

        result = runner.invoke(main_app, ["--json", "skill", "list"])
        assert result.exit_code == 0
        import json

        data = json.loads(result.stdout)
        assert data["ok"] is True
        assert "skills" in data["data"]
        bundled = set(_list_skill_dirs())
        listed = {skill["name"] for skill in data["data"]["skills"]}
        # 与真实捆绑集合精确比对：数量漂移（多/少任何一个）都必须红，
        # 不能用 `>= N` —— N 是历史快照，挡不住新增 Skill 后的文档漂移。
        assert listed == bundled, (
            f"`map skill list` 与捆绑 Skill 集合不一致："
            f"多出 {sorted(listed - bundled) or '无'}，缺失 {sorted(bundled - listed) or '无'}"
        )
        # Each skill entry should have name and has_skill_md
        for skill in data["data"]["skills"]:
            assert "name" in skill
            assert "has_skill_md" in skill

    def test_json_install(self, tmp_path):
        """--json skill install should output {ok: true, data: {installed: [...]}}."""
        from cli.main import app as main_app

        target = tmp_path / "skills"
        result = runner.invoke(
            main_app,
            ["--json", "skill", "install", "--target", str(target)],
        )
        assert result.exit_code == 0
        import json

        data = json.loads(result.stdout)
        assert data["ok"] is True
        assert "installed" in data["data"]
        assert "skipped" in data["data"]
        assert data["data"]["installed_count"] == len(_list_skill_dirs())

    def test_non_json_list_still_human_readable(self):
        """Default (non-json) skill list should output human-readable table."""
        # Reset module-level format that may have been set by --json tests
        from cli.main import _cli_options
        original = _cli_options.get("format")
        _cli_options["format"] = "yaml"
        try:
            result = runner.invoke(skill_app, ["list"])
            assert result.exit_code == 0
            # T42：render_table 输出大写表头
            assert "SKILL NAME" in result.stdout
            assert "VERSION" in result.stdout
        finally:
            _cli_options["format"] = original


class TestSkillInventoryDocumentation:
    """面向用户的文档里列举的 Skill 清单必须与真实捆绑集合一致。

    防止重演：MAP_AGENT_PROMPT.md 曾长期写着「安装 5 个 Skill」并漏列
    `experiment-executor`（当时实际已分发 6 个），而旧断言是 `>= 5`，
    既挡不住漏项、也挡不住将来新增第 7 个时的文档滞后。
    """

    AGENT_PROMPT = REPO_ROOT / "MAP_AGENT_PROMPT.md"

    def test_agent_prompt_names_every_skill(self):
        """每个捆绑 Skill 都必须在该文档里以 `skill-name` 形式出现至少一次。"""
        text = self.AGENT_PROMPT.read_text(encoding="utf-8")
        bundled = set(_list_skill_dirs())
        missing = [name for name in sorted(bundled) if f"`{name}`" not in text]
        assert not missing, (
            f"{self.AGENT_PROMPT.name} 漏列 Skill {missing}；"
            f"该文件是分发给用户 Agent 的产品面，漏一个就等于用户永远不知道它的存在"
        )

    def test_agent_prompt_skill_count_matches(self):
        """文档里写死的「安装 N 个 Skill」必须等于实际捆绑数量。"""
        text = self.AGENT_PROMPT.read_text(encoding="utf-8")
        match = re.search(r"安装\s*(\d+)\s*个\s*Skill", text)
        assert match, f"{self.AGENT_PROMPT.name} 未找到「安装 N 个 Skill」字样"
        bundled = set(_list_skill_dirs())
        assert int(match.group(1)) == len(bundled), (
            f"{self.AGENT_PROMPT.name} 写的是 {match.group(1)} 个 Skill，"
            f"实际捆绑 {len(bundled)} 个：{sorted(bundled)}"
        )
