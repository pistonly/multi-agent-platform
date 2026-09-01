"""work_kinds registry 单测（实验 d559f431 / work-kind-dispatch-single-source I1
+ 实验 8b1d20a1 I1 扩字段 required_role / obligation_whitelist_exempt）。

I1 范围：registry 结构完整性（kind 唯一、skill/clear_action 非空、get 查询）+
与 wake.md 分发表首列 kind 集合的一致性（A3 CI diff 的雏形；完整标记块整行
diff 属 I3）。

实验 8b1d20a1 增量（I1 验收）：
- required_role 字段枚举约束（host/participant/reviewer/all）
- obligation_whitelist_exempt 与 reviewer 角色的关联约束（A4 硬边界）
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
        # plan-mode-direct-execution-productization I1: the executor Skill
        # is the routing target for executor_assignments. Creation of the
        # actual Skill markdown is deferred (I2 next round) — the kind
        # registry keeps referencing it so waker dispatch stays
        # single-sourced.
        "experiment-executor",
    }
    spec = get_kind_spec(kind)
    assert spec is not None
    assert spec.skill in known, f"{kind}: skill {spec.skill!r} 不在已知 Skill 集"


# 实验 8b1d20a1 I1 增量测试


_ALLOWED_ROLES = frozenset({"host", "participant", "reviewer", "all"})

# reviewer 角色的 obligation kind（A4 硬边界：必须豁免白名单过滤）
_REVIEWER_OBLIGATION_KINDS = frozenset(
    {
        "pending_reviews",
        "pending_result_reviews",
        "pending_replies",
    }
)


@pytest.mark.parametrize(
    "kind", [spec.kind for spec in WORK_ITEM_KINDS]
)
def test_required_role_enum(kind: str) -> None:
    """required_role 必须是 host/participant/reviewer/all 之一。"""
    spec = get_kind_spec(kind)
    assert spec is not None
    assert spec.required_role in _ALLOWED_ROLES, (
        f"{kind}: required_role {spec.required_role!r} 不在枚举 "
        f"{sorted(_ALLOWED_ROLES)}"
    )


def test_required_role_no_default_any() -> None:
    """全部 13 条 kind 实例必须显式填 required_role（默认 all 不接受）。

    防止后续添加 kind 时漏填字段——所有 kind 的角色归属应该是显式决策。
    """
    for spec in WORK_ITEM_KINDS:
        assert spec.required_role != "all" or spec.kind in {
            "mentions",
            "unread_change",
        }, (
            f"{spec.kind}: required_role 显式填 all 仅允许 mentions/unread_change"
        )


def test_reviewer_obligation_whitelist_exempt() -> None:
    """A4 硬边界：reviewer 角色的 obligation kind 必须豁免白名单。

    pending_reviews / pending_result_reviews / pending_replies 是 reviewer
    唯一可清理的 obligation kind；必须 obligation_whitelist_exempt=True，
    保证 reviewer 在未参与任何 topic 的情况下也能收 fan-out。
    """
    for kind in _REVIEWER_OBLIGATION_KINDS:
        spec = get_kind_spec(kind)
        assert spec is not None
        assert spec.required_role == "reviewer", (
            f"{kind}: 期望 required_role=reviewer，实际 {spec.required_role!r}"
        )
        assert spec.obligation_whitelist_exempt is True, (
            f"{kind}: reviewer obligation 必须豁免白名单过滤"
        )


def test_non_reviewer_obligation_no_exempt() -> None:
    """非 reviewer 的 obligation kind 默认 obligation_whitelist_exempt=False。

    host/participant/all 的 obligation kind 不应豁免白名单过滤——否则
    reviewer 会再次收到不必要唤醒。unread_change 是 contextual kind 也
    不豁免（A2：contextual 按白名单过滤）。
    """
    for spec in WORK_ITEM_KINDS:
        if spec.kind in _REVIEWER_OBLIGATION_KINDS:
            continue
        assert spec.obligation_whitelist_exempt is False, (
            f"{spec.kind}: 非 reviewer obligation 不应豁免白名单"
        )


def test_render_kinds_md_includes_new_columns() -> None:
    """render_kinds_md 输出包含 required_role / obligation_whitelist_exempt 列。"""
    rendered = render_kinds_md()
    header = rendered.splitlines()[0]
    assert "required_role" in header
    assert "obligation_whitelist_exempt" in header
    # 数据行也必须含字段值（任意取 mentions 行作 spot check）
    assert "| all | False |" in rendered, "数据行应输出 role/exempt 两列值"
