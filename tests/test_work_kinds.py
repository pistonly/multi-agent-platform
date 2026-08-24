"""work_kinds registry 单测（实验 d559f431 / work-kind-dispatch-single-source I1）。

I1 范围：registry 结构完整性（kind 唯一、skill/clear_action 非空、get 查询）+
与 wake.md 分发表首列 kind 集合的一致性（A3 CI diff 的雏形；完整标记块整行
diff 属 I3）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from server.services.work_kinds import WORK_ITEM_KINDS, get_kind_spec, render_kinds_md

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
    """wake.md 标记块内表格 == render_kinds_md() 渲染（A3/D8 整行逐字符 diff）。

    块缺失视为漂移（fail-safe），不允许静默跳过。
    """
    text = WAKE_MD.read_text(encoding="utf-8")
    begin = "<!-- BEGIN:kind-dispatch"
    end = "<!-- END:kind-dispatch -->"
    assert begin in text and end in text, (
        "wake.md 缺 kind-dispatch 生成标记块（被删/被挪即漂移，恢复见 "
        "map work --kinds --kinds-format md）"
    )
    section = text[text.index(begin) : text.index(end) + len(end)]
    # 标记行之间的块体
    body_lines = [
        line
        for line in section.splitlines()
        if not line.strip().startswith("<!--")
    ]
    rendered_lines = render_kinds_md().splitlines()
    assert body_lines == rendered_lines, (
        "registry 渲染与 wake.md 标记块 drift："
        f"\n仅渲染={sorted(set(rendered_lines) - set(body_lines))}"
        f"\n仅 wake.md={sorted(set(body_lines) - set(rendered_lines))}"
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
