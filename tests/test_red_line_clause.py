"""红线条款副本漂移检测（实验 e6d23886 I9 补建，修复悬空引用）。

lib/red_line_clause.py 是单一真相源（单源常量 + 「SKILL.md 嵌入字面
一致副本」设计约束见其 docstring）：4 个 SKILL.md 与
map-project-collab/references/wake.md 嵌入一致副本，cli/skills/** 为
wheel 分发同步副本（随包分发，`map skill install` 落到用户项目）。

本测试断言三层一致，任一处单方面修改即红：
1. 嵌入副本覆盖单源常量：RED_LINE_CLAUSE / INCIDENT_TRIGGER 必选；
   文件引用行声明的 PERSONA_INCIDENT_RESPONSE[p] 必须真实嵌入
2. 嵌入文件引用行指向本测试——引用与守卫互为锚点，防守卫被静默摘除
3. cli/skills 分发副本与 .cursor/skills 源全文件一致（__init__.py 为
   包化新增，不在镜像范围）
"""
from __future__ import annotations

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

# (相对 .cursor/skills 的嵌入文件, 该文件声明必嵌的 persona 响应键)
EMBEDDINGS: list[tuple[str, str | None]] = [
    ("experiment-host/SKILL.md", "host"),
    ("experiment-executor/SKILL.md", "participant"),
    ("experiment-reviewer/SKILL.md", "reviewer"),
    ("topic-host/SKILL.md", "participant"),
    ("map-project-collab/references/wake.md", None),
]


@pytest.mark.parametrize(("rel", "persona"), EMBEDDINGS)
def test_embedding_matches_single_source(rel: str, persona: str | None):
    content = (CURSOR_SKILLS / rel).read_text(encoding="utf-8")
    assert RED_LINE_CLAUSE in content, rel
    assert INCIDENT_TRIGGER in content, rel
    if persona is not None:
        assert PERSONA_INCIDENT_RESPONSE[persona] in content, rel


@pytest.mark.parametrize(("rel", "persona"), EMBEDDINGS)
def test_embedding_cites_drift_guard(rel: str, persona: str | None):
    del persona
    content = (CURSOR_SKILLS / rel).read_text(encoding="utf-8")
    assert "tests/test_red_line_clause.py" in content, rel


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
