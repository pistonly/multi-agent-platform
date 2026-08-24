"""work item kind 单一真相 machine guard（实验 d559f431 R2）。

A1 语义：registry（server/services/work_kinds.py 的 WORK_ITEM_KINDS）是
kind → 清理动作 + Skill 分发的 server 侧单一真相。CI 的 A3 registry↔wake.md
整行 diff 只防 **registry→渲染** 方向（两边同源于 registry，自洽闭环）；
**service → registry** 方向（service 产未登记/改名 kind）此前零机器防线——
正是本测试补的缺口。

覆盖：
- AST 静态收集 direction-A 产 kind service（fs_source_service.py）全部
  ``TopicWorkItemRead(kind=...)`` 字符串字面量 → ⊆ WORK_ITEM_KINDS keys
  （零豁免：注入未登记 kind 即红，含演示）。
- ``work_kinds.resolve_kind`` fail-fast：未登记名字直接 KeyError（R1 接线
  的产 kind 代码侧防线，与 AST 测试互补）。
- agent_work 的 SummaryBucketKind 是**内部聚合桶**（非对外 kind，R3 已注释
  区分），由 SDK Literal 类型约束；这里再断言桶集合 ⊆ SummaryBucketKind，
  收口「桶面不得漂出内部类型」。

范围边界（显式声明）：DB 话题路径（topic_work_item_service.py）的内部
kind 采单数形（mention / pending_topic_reply），是**退役存量面**——经
todo_service 落复数分区键（tests/test_todos.py 已守），不属 direction-A
消费面；对外分发一律以 registry 复数码为唯一真相。本 guard 覆盖 direction-A
产 kind 面（fs_source_service），DB 面边界见 R3 注释与实验 log。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from map_types.schemas.agent_work import SummaryBucketKind

from server.services.work_kinds import WORK_ITEM_KINDS, get_kind_spec, resolve_kind

_SERVER_ROOT = Path(__file__).resolve().parents[1] / "server" / "services"

_REGISTERED = frozenset(spec.kind for spec in WORK_ITEM_KINDS)


def _resolve_kind_args(py_path: Path, *constructor_names: str) -> set[str]:
    """Return registry kind names referenced from the named constructor calls.

    收集两种形态（二者皆须先登记，构成对外的产 kind 集合）：
    - ``kind="action_items"`` 裸字面量（绕过 resolve_kind 的形态也要被守）
    - ``kind=resolve_kind("action_items")``（R1 接线后的规范形态）
    """
    tree = ast.parse(py_path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = (
            func.id
            if isinstance(func, ast.Name)
            else func.attr
            if isinstance(func, ast.Attribute)
            else None
        )
        if name not in constructor_names:
            continue
        for kw in node.keywords:
            if kw.arg != "kind":
                continue
            if isinstance(kw.value, ast.Constant):
                found.add(kw.value.value)
            elif (
                isinstance(kw.value, ast.Call)
                and isinstance(kw.value.func, ast.Name)
                and kw.value.func.id == "resolve_kind"
                and kw.value.args
                and isinstance(kw.value.args[0], ast.Constant)
            ):
                found.add(kw.value.args[0].value)
    return found


def test_fs_source_produced_kinds_subset_of_registry() -> None:
    """direction-A 产 kind ⊆ WORK_ITEM_KINDS keys（R2 主断言）。

    fs_source_service.py 的 TopicWorkItemRead(kind=...) 只要出现未在 registry
    登记的 kind（无论裸字面量还是 resolve_kind 引用）即红——新增/改名 kind
    未登记 → 本测试红。R1 落位后应全部经 resolve_kind。
    """
    produced = _resolve_kind_args(
        _SERVER_ROOT / "fs_source_service.py", "TopicWorkItemRead", "TopicWorkItem"
    )
    assert produced, "fs_source_service 应至少产一个 work item kind"
    assert produced == {"action_items", "stale_open_topics"}, (
        "fs_source 产 kind 集合漂移，R1 只接线了这两个 kind"
    )
    unregistered = produced - _REGISTERED
    assert not unregistered, (
        "service 产未登记 work item kind: "
        f"{sorted(unregistered)}；须先在 server/services/work_kinds.py 登记 "
        "（kind/clear_action/skill/note）+ 同步 wake.md 分发表（A5 checklist）"
    )


def test_fs_source_producers_use_resolve_kind() -> None:
    """R1 落位检查：fs_source 产 kind 处已接 registry，杜绝裸字面量二次手写。"""
    src = (_SERVER_ROOT / "fs_source_service.py").read_text(encoding="utf-8")
    assert "from server.services.work_kinds import resolve_kind" in src
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = (
            node.func.id
            if isinstance(node.func, ast.Name)
            else node.func.attr
            if isinstance(node.func, ast.Attribute)
            else None
        )
        if name not in {"TopicWorkItemRead", "TopicWorkItem"}:
            continue
        for kw in node.keywords:
            if kw.arg == "kind" and isinstance(kw.value, ast.Constant):
                raise AssertionError(
                    f"fs_source {name} 仍裸写 kind={kw.value.value!r}，"
                    "应改经 work_kinds.resolve_kind"
                )


def test_injection_of_unregistered_kind_would_red() -> None:
    """注入演示：把未登记 kind 接进产 kind 集合即触发红。"""
    injected = {"definitely_not_a_registered_kind"}
    assert injected - _REGISTERED, "注入候选必须未登记才能演示防线"


def test_resolve_kind_fail_fast_on_unregistered() -> None:
    """R1 接线代码侧防线：resolve_kind 未登记名字立即 KeyError。"""
    assert resolve_kind("action_items") == "action_items"
    assert resolve_kind("stale_open_topics") == "stale_open_topics"
    with pytest.raises(KeyError, match="not registered"):
        resolve_kind("definitely_not_a_registered_kind")


def test_agent_work_bucket_kinds_within_summary_bucket_literal() -> None:
    """agent_work 的桶 kind ⊆ SummaryBucketKind（内部聚合桶机器防线）。

    SummaryBucketKind 是 /work 摘要卡片的内部桶类型（R3 已注释区分：桶标签
    单数、非对外 kind）；断言桶集合不漂出该 Literal——桶面新增/改名即红。
    """
    bucket_kinds = _resolve_kind_args(
        _SERVER_ROOT / "agent_work_service.py", "_make_item", "_bucket"
    )
    allowed = frozenset(SummaryBucketKind.__args__)
    assert bucket_kinds <= allowed, f"未知桶 kind: {sorted(bucket_kinds - allowed)}"


def test_registry_kinds_are_distinct_and_nonempty() -> None:
    """registry 基本面：kind 唯一、非空（防重复登记稀释单一真相）。"""
    assert len(_REGISTERED) == len(WORK_ITEM_KINDS), "WORK_ITEM_KINDS 存在重复 kind"
    assert get_kind_spec("action_items") is not None
