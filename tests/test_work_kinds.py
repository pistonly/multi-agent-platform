"""work_kinds registry 单测（实验 d559f431 / work-kind-dispatch-single-source I1）。

I1 范围：registry 结构完整性（kind 唯一、skill/clear_action 非空、get 查询）+
与 wake.md 分发表首列 kind 集合的一致性（A3 CI diff 的雏形；完整标记块整行
diff 属 I3）。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from server.services.work_kinds import WORK_ITEM_KINDS, get_kind_spec

WAKE_MD = (
    Path(__file__).resolve().parents[1]
    / ".cursor/skills/map-project-collab/references/wake.md"
)


def test_kinds_unique() -> None:
    kinds = [spec.kind for spec in WORK_ITEM_KINDS]
    assert len(kinds) == len(set(kinds)), "kind 名重复"


def test_fields_nonempty() -> None:
    for spec in WORK_ITEM_KINDS:
        assert spec.kind, f"{spec}: kind 空"
        assert spec.clear_action, f"{spec.kind}: clear_action 空"
        assert spec.skill, f"{spec.kind}: skill 空（A1 必需字段）"


def test_get_kind_spec_hit_and_miss() -> None:
    assert get_kind_spec("mentions") is not None
    assert get_kind_spec("action_items") is not None
    assert get_kind_spec("no_such_kind") is None


def test_registry_matches_wake_md_dispatch_table() -> None:
    """registry kind 集合 == wake.md 分发表首列 kind 集合（合并行全提取）。"""
    text = WAKE_MD.read_text(encoding="utf-8")
    start = text.index("## kind → 清理动作")
    end = text.index("### FS 话题速查")
    section = text[start:end]

    table_kinds: set[str] = set()
    for cell in re.findall(r"^\|([^|]+)\|", section, flags=re.MULTILINE):
        # 去掉括号内修饰（如 `（FS 话题，reason=`fs_file_missing`）`），
        # 只留并列 kind 项（`pending_reviews` / `pending_result_reviews` ...）
        cell_wo_paren = re.sub(r"（[^）]*）|\([^)]*\)", "", cell)
        table_kinds.update(re.findall(r"`([a-z_]+)`", cell_wo_paren))

    assert table_kinds, "wake.md 分发表解析为空，表结构可能已变"
    registry_kinds = {spec.kind for spec in WORK_ITEM_KINDS}
    assert registry_kinds == table_kinds, (
        f"registry 与 wake.md 表 drift：仅 registry={sorted(registry_kinds - table_kinds)} "
        f"仅 wake.md={sorted(table_kinds - registry_kinds)}"
    )


@pytest.mark.parametrize(
    "kind", [spec.kind for spec in WORK_ITEM_KINDS]
)
def test_skill_is_known_persona_skill(kind: str) -> None:
    """skill 字段指向真实 Skill 名（A1：归属 Skill 是断链防线）。"""
    known = {
        "map-project-collab",
        "topic-host",
        "topic-participant",
        "experiment-host",
        "experiment-reviewer",
    }
    spec = get_kind_spec(kind)
    assert spec is not None
    assert spec.skill in known, f"{kind}: skill {spec.skill!r} 不在已知 Skill 集"
