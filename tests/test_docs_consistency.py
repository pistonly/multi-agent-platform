"""docs/ 内容一致性护栏（PRD v0.11 M50）。

M50 修复了文档与 CLI / 部署事实之间的漂移；本文件用可执行断言把
关键约定钉住，防止同类漂移再次合入：

* M50A QUICKSTART 中 ``topic create`` 示例参数与 CLI 定义一致（``--description``）
* M50B 文档默认端口唯一：本仓 Docker override 恒生效，API 宿主端口为 ``8001``
* M50C ``docs/INDEX.md`` 与根 ``README.md`` 的「现行版本」指向与
  ``docs/prd/README.md`` 解析出的现行草案一致
* M50D Skill 不再教 Agent 往 ``docs/topics/``、``docs/experiments/`` 写新内容
* M50E ``AGENTS.md`` 保持薄索引（行为细节在 Skill，命令细节在 commands.md）
* M50F 孤儿文件 ``agents/openai.yaml`` 不再回归

设计原则：断言「解析出的事实」而不是硬编码版本号，升级 PRD 版本时
不需要同步改这里的多数用例；注入漂移样例（改端口 / 改参数 / 改指向）
时对应用例必须失败。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# 历史性内容（归档 PRD / 评审证据 / FS 事实源存量）不参与端口一致性检查。
PORT_CHECK_SKIP_DIRS = {
    ".git",
    ".map",
    ".pytest_cache",
    ".trae-cn",
    ".venv",
    "__pycache__",
    "archive",
    "build",
    "dist",
    "map",
    "node_modules",
    "reports",
    "venv",
}

# M50D 涉及的 Skill 文件（cli/skills 与 .cursor/skills 两份镜像）。
LEGACY_PATH_SKILL_FILES = [
    "topic-host/references/host-checklist.md",
    "topic-participant/references/participant-checklist.md",
    "experiment-host/SKILL.md",
    "experiment-reviewer/SKILL.md",
    "map-project-collab/references/commands.md",
]
SKILL_MIRRORS = ["cli/skills", ".cursor/skills"]


def _read(path: Path) -> str:
    assert path.is_file(), f"expected file missing: {path}"
    return path.read_text(encoding="utf-8")


def _current_prd_versions() -> list[str]:
    """Parse ``docs/prd/README.md`` 现行草案 section → ['v0.10', 'v0.11', ...]."""
    text = _read(REPO_ROOT / "docs" / "prd" / "README.md")
    match = re.search(r"^##\s*现行草案.*?(?=^##\s|\Z)", text, re.M | re.S)
    assert match, "docs/prd/README.md must have a '## 现行草案' section"
    return sorted(
        {f"v{m}" for m in re.findall(r"\]\(\./v([\d.]+)\.md\)", match.group(0))}
    )


def _iter_port_check_files() -> list[Path]:
    files: list[Path] = []
    for path in REPO_ROOT.rglob("*.md"):
        if not any(part in PORT_CHECK_SKIP_DIRS for part in path.parts):
            files.append(path)
    return sorted(files)


class TestQuickstartMatchesCli:
    """M50A：QUICKSTART 示例参数与 CLI 定义一致。"""

    def test_topic_create_uses_description_not_body(self) -> None:
        """QUICKSTART 话题创建演示走 ``map topic create``（FS 文件夹，统一入口）。"""
        text = _read(REPO_ROOT / "docs" / "QUICKSTART.md")
        create_lines = [
            line.strip()
            for line in text.splitlines()
            if re.search(r"map[^\n]*\btopic create\b", line)
        ]
        assert create_lines, "QUICKSTART should demonstrate `map topic create`"
        for line in create_lines:
            assert "--body" not in line, (
                f"`topic create` has no --body param (use --title/--slug/--participants): {line}"
            )
            assert "--title" in line and "--slug" in line, (
                f"`topic create` example missing --title/--slug: {line}"
            )

    def test_bootstrap_api_url_uses_default_port(self) -> None:
        text = _read(REPO_ROOT / "docs" / "QUICKSTART.md")
        url_lines = [
            line.strip()
            for line in text.splitlines()
            if re.search(r"--api-url\s+https?://", line)
        ]
        assert url_lines, "QUICKSTART should show a bootstrap --api-url example"
        for line in url_lines:
            assert "localhost:18400" in line, (
                f"bootstrap --api-url should use the repo default port 18400: {line}"
            )


class TestDefaultPortUnique:
    """M50B：文档默认端口值唯一（18400）。"""

    @pytest.mark.parametrize("legacy_port", ["localhost:8000", "localhost:8001"])
    def test_no_docs_use_legacy_default_port(self, legacy_port: str) -> None:
        offenders = [
            str(p.relative_to(REPO_ROOT))
            for p in _iter_port_check_files()
            if legacy_port in p.read_text(encoding="utf-8")
        ]
        assert not offenders, (
            f"docs referencing {legacy_port} found (the documented default API "
            f"port is 18400; keep the documented default unique): {offenders}"
        )

    def test_compose_publishes_18400(self) -> None:
        """护栏的前提：基础 compose 确实把 API 映射到宿主 18400（容器内 8000）。"""
        compose = REPO_ROOT / "docker-compose.yml"
        text = compose.read_text(encoding="utf-8")
        assert re.search(r"\"?18400:8000\"?", text), (
            "docker-compose.yml should map host 18400 -> container 8000"
        )


class TestCurrentPrdPointers:
    """M50C：INDEX / README 的现行版本指向与 prd/README.md 一致。"""

    def test_current_versions_exist(self) -> None:
        current = _current_prd_versions()
        assert current, "prd/README.md 现行草案 section lists no versions"
        for version in current:
            assert (REPO_ROOT / "docs" / "prd" / f"{version}.md").is_file(), (
                f"现行草案 lists {version} but docs/prd/{version}.md is missing"
            )

    @pytest.mark.parametrize("entry", ["docs/INDEX.md", "README.md"])
    def test_entry_links_current_versions(self, entry: str) -> None:
        text = _read(REPO_ROOT / entry)
        for version in _current_prd_versions():
            assert f"prd/{version}.md" in text, (
                f"{entry} should link current PRD {version} "
                f"(docs/prd/README.md is the source of truth)"
            )

    @pytest.mark.parametrize("entry", ["docs/INDEX.md", "README.md"])
    def test_entry_does_not_claim_retired_version_current(self, entry: str) -> None:
        """「现行…vX.Y」声明只能指向现行草案集合，防止旧指向残留。"""
        text = _read(REPO_ROOT / entry)
        current = set(_current_prd_versions())
        claims = re.findall(r"现行[^。\n|（(]{0,16}v(\d+\.\d+)", text)
        stale = {f"v{c}" for c in claims} - current
        assert not stale, (
            f"{entry} claims retired version(s) {sorted(stale)} as 现行; "
            f"current set is {sorted(current)}"
        )


class TestSkillPaths:
    """M50D：Skill 内容路径约定统一为 map/。"""

    @pytest.mark.parametrize("mirror", SKILL_MIRRORS)
    @pytest.mark.parametrize("skill_file", LEGACY_PATH_SKILL_FILES)
    def test_no_legacy_content_path_guidance(
        self, mirror: str, skill_file: str
    ) -> None:
        path = REPO_ROOT / mirror / skill_file
        if not path.exists():
            pytest.skip(f"mirror file absent: {path}")
        text = _read(path)
        assert "docs/topics/" not in text and "docs/experiments/" not in text, (
            f"{path} still teaches legacy docs/ content paths; "
            f"use map/topics/<slug>/ and map/experiments/<slug>/"
        )

    def test_openai_yaml_orphan_stays_deleted(self) -> None:
        for mirror in SKILL_MIRRORS:
            orphan = REPO_ROOT / mirror / "map-project-collab" / "agents" / "openai.yaml"
            assert not orphan.exists(), (
                f"orphan runtime config resurrected: {orphan}"
            )


class TestAgentsMdStaysThin:
    """M50E：AGENTS.md 是薄索引，行为细节归 Skill。"""

    def test_line_budget(self) -> None:
        text = _read(REPO_ROOT / "AGENTS.md")
        assert len(text.splitlines()) <= 80, (
            "AGENTS.md grew beyond the 80-line thin-index budget; "
            "move behavior/command details into skills/.../references/"
        )

    def test_delegates_to_commands_reference(self) -> None:
        text = _read(REPO_ROOT / "AGENTS.md")
        assert "commands.md" in text, (
            "AGENTS.md should point to the skills command reference "
            "instead of duplicating command blocks"
        )
