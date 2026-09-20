"""docs/ 内容一致性护栏（PRD v0.11 M50）。

M50 修复了文档与 CLI / 部署事实之间的漂移；本文件用可执行断言把
关键约定钉住，防止同类漂移再次合入：

* M50A QUICKSTART 中 ``fs topic-create`` 示例参数与 CLI 定义一致（``--title`` / ``--slug``）
* M50B 文档默认端口唯一：本仓 Docker override 恒生效，API 宿主端口为 ``8001``
* M50C ``docs/INDEX.md`` 与根 ``README.md`` 的「现行版本」指向与
  ``docs/prd/README.md`` 解析出的现行草案一致
* M50D Skill 不再教 Agent 往 ``docs/topics/``、``docs/experiments/`` 写新内容
* M50E ``AGENTS.md`` 保持薄索引（行为细节在 Skill，命令细节在 commands.md）
* M50F 孤儿文件 ``agents/openai.yaml`` 不再回归
* M50G Skill 模板的 ``topic close --reason`` 取值必须落在
  ``map_fs.validation.CLOSE_REASON_LEGAL`` 内（issue #1 漂移回归）
* M50H ``docs/``、两份 Skill 镜像与根级 md 内的**相对链接必须指向存在的目标**
  （含 ``#锚点``）：归档文档整体挪目录后相对路径会静默失效，此前无人发现

设计原则：断言「解析出的事实」而不是硬编码版本号，升级 PRD 版本时
不需要同步改这里的多数用例；注入漂移样例（改端口 / 改参数 / 改指向）
时对应用例必须失败。
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote

import pytest
from map_fs import validation as fs_validation

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

# M50D 涉及的 Skill 文件（cli/skills 与 .agent/skills 两份镜像）。
LEGACY_PATH_SKILL_FILES = [
    "topic-host/references/host-checklist.md",
    "topic-participant/references/participant-checklist.md",
    "experiment-host/SKILL.md",
    "experiment-executor/SKILL.md",
    "experiment-reviewer/SKILL.md",
    "map-project-collab/SKILL.md",
    "map-project-collab/references/commands.md",
]
SKILL_MIRRORS = ["cli/skills", ".agent/skills"]


def _read(path: Path) -> str:
    assert path.is_file(), f"expected file missing: {path}"
    return path.read_text(encoding="utf-8")


def _parse_prd_version_tuple(version: str) -> tuple[int, ...]:
    body = version[1:] if version.startswith("v") else version
    return tuple(int(part) for part in body.split("."))


def _current_prd_versions() -> list[str]:
    """Parse ``docs/prd/README.md`` 现行草案 section → ['v0.10', 'v0.11', ...]."""
    text = _read(REPO_ROOT / "docs" / "prd" / "README.md")
    match = re.search(r"^##\s*现行草案.*?(?=^##\s|\Z)", text, re.M | re.S)
    assert match, "docs/prd/README.md must have a '## 现行草案' section"
    return sorted(
        {f"v{m}" for m in re.findall(r"\]\(\./v([\d.]+)\.md\)", match.group(0))}
    )


def _latest_prd_root_version() -> str:
    """Largest ``docs/prd/v*.md`` (non-recursive; archive/ is not included)."""
    versions: list[str] = []
    for path in (REPO_ROOT / "docs" / "prd").glob("v*.md"):
        match = re.fullmatch(r"(v\d+(?:\.\d+)*)\.md", path.name)
        if match:
            versions.append(match.group(1))
    assert versions, "docs/prd/ should contain at least one v*.md"
    return max(versions, key=_parse_prd_version_tuple)


def _iter_port_check_files() -> list[Path]:
    files: list[Path] = []
    for path in REPO_ROOT.rglob("*.md"):
        if not any(part in PORT_CHECK_SKIP_DIRS for part in path.parts):
            files.append(path)
    return sorted(files)


class TestQuickstartMatchesCli:
    """M50A：QUICKSTART 示例参数与 CLI 定义一致。"""

    def test_topic_create_uses_description_not_body(self) -> None:
        """QUICKSTART 话题创建演示走 ``map topic create``。"""
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

    def test_latest_prd_root_version_is_listed_current(self) -> None:
        """docs/prd/ 根目录版本号最大的 v*.md 必须出现在「现行草案」段。"""
        latest = _latest_prd_root_version()
        current = set(_current_prd_versions())
        assert latest in current, (
            f"docs/prd/ root latest {latest} must appear in 「现行草案」; "
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


# 退役的 close_reason 取值：出现在 skill 模板的 close 命令里即为漂移。
RETIRED_CLOSE_REASONS = ("no_experiment_needed", "superseded")


class TestSkillCloseReasonConsistency:
    """M50G：Skill 模板的 ``topic close --reason`` 必须与枚举单一真值同源。

    背景（issue #1）：0.16.0 wheel 把 ``CLOSE_REASON_LEGAL`` 收窄为 4 值，
    但 ``cli/skills`` 分发的模板仍教退役值 ``no_experiment_needed``——照模板
    执行的 host agent 必然撞 ``InvalidCloseReasonError``；下游只能手改已安装
    模板，而 ``map skill upgrade --force`` 又会把手改覆盖回旧文案。
    根因是「模板文案」与「枚举定义」之间没有可执行断言，本类补上这一层。
    """

    _IDENT = re.compile(r"[A-Za-z0-9_]+")

    @staticmethod
    def _logical_lines(text: str) -> list[tuple[int, str]]:
        """把以反斜杠续行的命令拼成一条逻辑行（行号取该逻辑行的起始行）。"""
        merged: list[tuple[int, str]] = []
        buffer = ""
        start = 1
        for lineno, raw in enumerate(text.splitlines(), start=1):
            line = raw.rstrip()
            if not buffer:
                start = lineno
            if line.endswith("\\"):
                buffer += line[:-1] + " "
                continue
            merged.append((start, buffer + line))
            buffer = ""
        if buffer:
            merged.append((start, buffer))
        return merged

    @classmethod
    def _close_reasons(cls, text: str) -> list[tuple[int, str]]:
        """返回 ``topic close`` 命令里的 ``(行号, reason 取值)``。

        只看 ``topic close`` 之后的 ``--reason``，避免误伤
        ``action-item cancel --reason "..."`` / ``project config flag set
        --reason ...`` 这类自由文本用法；占位符（``...`` / ``"..."``）不产
        生标识符，自动跳过。
        """
        found: list[tuple[int, str]] = []
        for lineno, line in cls._logical_lines(text):
            pos = line.find("topic close")
            if pos < 0:
                continue
            for match in re.finditer(r"--reason\s+(\S+)", line[pos:]):
                ident = cls._IDENT.match(match.group(1))
                if ident:
                    found.append((lineno, ident.group(0)))
        return found

    @pytest.mark.parametrize("mirror", SKILL_MIRRORS)
    def test_close_reason_is_in_enum(self, mirror: str) -> None:
        """模板里每个 close --reason 取值都必须是当前合法枚举值。"""
        offenders: list[str] = []
        for path in sorted((REPO_ROOT / mirror).rglob("*.md")):
            for lineno, reason in self._close_reasons(_read(path)):
                if reason not in fs_validation.CLOSE_REASON_LEGAL:
                    rel = path.relative_to(REPO_ROOT)
                    offenders.append(f"{rel}:{lineno} --reason {reason}")
        assert not offenders, (
            "skill 模板教了非法 close_reason（照模板执行会被 "
            f"InvalidCloseReasonError 拒绝）；合法值 = "
            f"{sorted(fs_validation.CLOSE_REASON_LEGAL)}；违规：{offenders}"
        )

    @pytest.mark.parametrize("reason", RETIRED_CLOSE_REASONS)
    def test_no_retired_reason_in_close_commands(self, reason: str) -> None:
        """退役取值不得作为 close 命令参数回归（更精确的定位提示）。"""
        offenders: list[str] = []
        for mirror in SKILL_MIRRORS:
            for path in sorted((REPO_ROOT / mirror).rglob("*.md")):
                for lineno, found in self._close_reasons(_read(path)):
                    if found == reason:
                        rel = path.relative_to(REPO_ROOT)
                        offenders.append(f"{rel}:{lineno}")
        assert not offenders, (
            f"退役 close_reason '{reason}' 仍在 skill 模板的命令里：{offenders}；"
            f"改用 {sorted(fs_validation.CLOSE_REASON_LEGAL)} 之一"
        )

    @pytest.mark.parametrize("mirror", SKILL_MIRRORS)
    def test_close_examples_are_actually_scanned(self, mirror: str) -> None:
        """反向守卫：必须真解析到 close 示例，否则上面用例会空转假绿。"""
        scanned: list[str] = []
        for path in sorted((REPO_ROOT / mirror).rglob("*.md")):
            scanned += [
                f"{path.relative_to(REPO_ROOT)}:{lineno}"
                for lineno, _ in self._close_reasons(_read(path))
            ]
        assert scanned, (
            f"{mirror} 下未解析出任何 `topic close --reason` 示例；"
            f"要么模板被删空，要么解析器与模板写法已脱节"
        )


# =========================================================================
# M50H 相对链接护栏
#
# 背景：文档挪目录（如 docs/x.md -> docs/archive/x.md）时，其中的相对链接
# 会静默失效——既不报错，也不在任何门禁里变红，只有点进去的人才会发现。
# 本仓此前已有 33 处这类死链（绝大多数是归档快照上移一层后失效），全部靠
# 人工核对才发现，故补上可执行断言。
# =========================================================================

# 扫描范围：docs/ 全树、两份 Skill 镜像（分发面，同样有内部相对链接）、根级 md。
LINK_CHECK_ROOTS = ["docs", "cli/skills", ".agent/skills"]

# 归档快照：整体上移一层目录后其相对链接天然失效，属历史记录，不回填（见 M50H 背景）。
LINK_CHECK_SKIP_PARTS = {"archive"}

_MD_LINK = re.compile(r"\[[^\]]*\]\(([^()\s]+)\)")
_ABSOLUTE_URL = re.compile(r"^(?:[A-Za-z][A-Za-z0-9+.-]*:|//)")


def _strip_fenced_code(text: str) -> str:
    """剔除 fenced code block 内容——其中的路径多为示例，不是真链接。"""
    kept: list[str] = []
    in_fence = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            kept.append("")
            continue
        kept.append("" if in_fence else line)
    return "\n".join(kept)


def _heading_anchor(title: str) -> str:
    """GitHub 风格标题锚点：小写、去标点、空白转 ``-``。"""
    slug = title.strip().lower()
    slug = re.sub(r"[`*_]", "", slug)
    slug = re.sub(r"[^\w\s-]", "", slug)
    return re.sub(r"\s+", "-", slug).strip("-")


def _heading_anchors(text: str) -> set[str]:
    return {
        _heading_anchor(match.group(1))
        for match in re.finditer(r"^#{1,6}\s+(.*?)\s*$", text, re.M)
    }


def _relative_link_targets(text: str) -> list[str]:
    """返回 md 里所有「相对」链接目标，外部 URL / 页内锚点 / 引用式不参与。"""
    targets: list[str] = []
    for match in _MD_LINK.finditer(_strip_fenced_code(text)):
        target = match.group(1)
        if _ABSOLUTE_URL.match(target) or target.startswith(("#", "[")):
            continue
        targets.append(target)
    return targets


def _link_problems(md_path: Path) -> list[str]:
    """返回该 md 内所有死链的人类可读描述（空列表 = 全部可达）。"""
    text = md_path.read_text(encoding="utf-8")
    problems: list[str] = []
    for target in _relative_link_targets(text):
        path_part, _, fragment = target.partition("#")
        if path_part:
            resolved = (md_path.parent / unquote(path_part)).resolve()
            if not resolved.exists():
                problems.append(f"{target} -> 目标不存在")
                continue
        else:  # 纯页内锚点
            resolved = md_path
        if fragment and resolved.is_file() and resolved.suffix == ".md":
            anchors = _heading_anchors(resolved.read_text(encoding="utf-8"))
            if unquote(fragment) not in anchors:
                problems.append(f"{target} -> 锚点不存在")
    return problems


def _iter_link_check_files() -> list[Path]:
    files: set[Path] = set(REPO_ROOT.glob("*.md"))
    for root in LINK_CHECK_ROOTS:
        base = REPO_ROOT / root
        if not base.is_dir():
            continue
        files.update(
            path
            for path in base.rglob("*.md")
            if not LINK_CHECK_SKIP_PARTS & set(path.relative_to(base).parts)
        )
    return sorted(files)


class TestDocsRelativeLinks:
    """M50H：markdown 相对链接必须指向存在的目标（含 ``#锚点``）。"""

    # 解析器必须真解析到这个量级的链接；否则下面的用例会因解析失败而空转假绿。
    MIN_SCANNED_LINKS = 200

    def test_no_broken_relative_links(self) -> None:
        offenders: list[str] = []
        for path in _iter_link_check_files():
            for problem in _link_problems(path):
                offenders.append(f"{path.relative_to(REPO_ROOT)}: {problem}")
        assert not offenders, (
            "发现死链（相对链接指向不存在的目标；归档快照请挪进 archive/ 目录，"
            "历史文档若需豁免请显式登记）：\n  " + "\n  ".join(sorted(offenders))
        )

    def test_relative_links_are_actually_scanned(self) -> None:
        total = sum(
            len(_relative_link_targets(_read(path)))
            for path in _iter_link_check_files()
        )
        assert total >= self.MIN_SCANNED_LINKS, (
            f"只解析出 {total} 条相对链接（下限 {self.MIN_SCANNED_LINKS}）；"
            f"要么文档被删空，要么链接正则与当前写法脱节"
        )

    def test_parser_flags_broken_target_and_ignores_urls(self, tmp_path: Path) -> None:
        """漂移注入自检：死链/死锚点必须被抓到，外部 URL 与合法链接不得误报。"""
        (tmp_path / "target.md").write_text("## Real Heading\n", encoding="utf-8")

        good = tmp_path / "good.md"
        good.write_text(
            "# Local\n\n[ok](./target.md#real-heading) "
            "[ext](https://example.com/x) [self](#local)\n",
            encoding="utf-8",
        )
        assert _link_problems(good) == [], _link_problems(good)

        bad = tmp_path / "bad.md"
        bad.write_text(
            "[missing file](./nope.md) [bad anchor](./target.md#nope)\n",
            encoding="utf-8",
        )
        problems = _link_problems(bad)
        assert len(problems) == 2, problems
        assert "目标不存在" in problems[0] and "锚点不存在" in problems[1]
