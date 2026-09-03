"""红线条款副本漂移检测 + Skill 分发面 dogfood 引用守卫。

lib/red_line_clause.py 是单一真相源（单源常量 + 「SKILL.md 嵌入字面
一致副本」设计约束见其 docstring）：4 个 SKILL.md 与
map-project-collab/references/wake.md 嵌入一致副本，cli/skills/** 为
wheel 分发同步副本（随包分发，`map skill install` 落到用户项目）。

本测试断言四层，任一处破坏即红：
1. 嵌入副本覆盖单源常量：RED_LINE_CLAUSE / INCIDENT_TRIGGER 必选；
   persona 专属 skill 必须真实嵌入对应 PERSONA_INCIDENT_RESPONSE 行
2. wheel 分发副本（cli/skills）与源（.cursor/skills）全文件一致
3. 分发面零 dogfood 引用：.cursor/skills 不含本仓库专属路径 / 实验
   hash / memory 文件名（外部用户项目中全部悬空）。操作指引中的
   `<slug>` 类占位符不在黑名单

分发面纪律的完整约定见仓库根 AGENTS.md「平台开发约定」节。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from lib.red_line_clause import (
    INCIDENT_TRIGGER,
    PERSONA_INCIDENT_RESPONSE,
    RED_LINE_CLAUSE,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CURSOR_SKILLS = REPO_ROOT / ".cursor" / "skills"
DIST_SKILLS = REPO_ROOT / "cli" / "skills"

# (相对 .cursor/skills 的嵌入文件, 该 skill 必嵌的 persona 响应键)
EMBEDDINGS: list[tuple[str, str | None]] = [
    ("experiment-host/SKILL.md", "host"),
    ("experiment-executor/SKILL.md", "participant"),
    ("experiment-reviewer/SKILL.md", "reviewer"),
    ("topic-host/SKILL.md", "participant"),
    ("map-project-collab/references/wake.md", None),
]

# 分发面禁止出现的本仓库专属引用（label, regex）。外部用户项目中
# 这些路径/编号全部悬空；`map/topics/<slug>/` 类占位符教学不命中。
DOGFOOD_REF_PATTERNS: list[tuple[str, str]] = [
    ("仓库测试路径 tests/test_*.py", r"tests/test_[a-z0-9_]+\.py"),
    ("8 位实验/提交 hash", r"\b[0-9a-f]{8}\b"),
    ("feedback memory 文件名", r"feedback_[a-z_]+\.md"),
    ("lib/ 模块路径", r"lib/[a-z_]+\.py"),
    ("map/topics/<具体slug> 路径", r"map/topics/[a-z0-9]"),
    ("逃出 skill 目录树的相对链接", r"\]\((?:\.\./){3,}"),
]


@pytest.mark.parametrize(("rel", "persona"), EMBEDDINGS)
def test_embedding_matches_single_source(rel: str, persona: str | None):
    content = (CURSOR_SKILLS / rel).read_text(encoding="utf-8")
    assert RED_LINE_CLAUSE in content, rel
    assert INCIDENT_TRIGGER in content, rel
    if persona is not None:
        assert PERSONA_INCIDENT_RESPONSE[persona] in content, rel


def test_dist_copy_matches_source():
    """wheel 分发副本（cli/skills）与源（.cursor/skills）全文件一致。"""
    dist_files = sorted(
        p.relative_to(DIST_SKILLS).as_posix()
        for p in DIST_SKILLS.rglob("*")
        if p.is_file() and p.name != "__init__.py"
    )
    assert dist_files, "cli/skills 分发目录为空（打包配置或目录被移动？）"
    for rel in dist_files:
        source, dist = CURSOR_SKILLS / rel, DIST_SKILLS / rel
        assert source.exists(), f"分发副本 {rel} 在源侧不存在（单向镜像被破坏）"
        assert dist.read_bytes() == source.read_bytes(), rel


@pytest.mark.parametrize(("label", "pattern"), DOGFOOD_REF_PATTERNS)
def test_dist_source_free_of_dogfood_refs(label: str, pattern: str):
    """分发面零 dogfood 引用：`.cursor/skills/**` 不含本仓库专属引用。"""
    rx = re.compile(pattern)
    offenders: list[str] = []
    for md in sorted(CURSOR_SKILLS.rglob("*.md")):
        for lineno, line in enumerate(
            md.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if rx.search(line):
                offenders.append(f"{md.relative_to(REPO_ROOT)}:{lineno}")
    assert not offenders, f"分发面发现{label}：{offenders}"
