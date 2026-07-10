"""合规 plan frontmatter 测试 helper。

来源:实验 0e0ce9ae — plan frontmatter fixture 升级。
`server/services/plan_marker_service.py` 在 commit 062c60e 落地了
`assert_plan_frontmatter_ok`,强制要求 plan 以 YAML frontmatter 开头、必填
`title` / `acceptance` / `evidence_keys` / `dependencies` 4 字段;缺则 422
`STATE_MACHINE_PLAN_MARKER_MISSING`。

历史测试 fixture 大量使用简单 plan(`"## plan"` / `"## 计划"` / `"p"` 等),
在 plan_marker 落地后会在 setup 阶段(`POST /experiments`)就 422,根本
走不到被测逻辑。本 helper 提供合规 frontmatter 工厂,供测试统一替换。
"""

from __future__ import annotations

from collections.abc import Iterable


def make_valid_plan(
    title: str = "t",
    body: str = "## plan",
    *,
    acceptance: Iterable[str] | None = None,
    evidence_keys: Iterable[str] | None = None,
    dependencies: Iterable[str] | None = None,
) -> str:
    """构造含合规 YAML frontmatter 的 plan markdown。

    4 个必填字段(title / acceptance / evidence_keys / dependencies)都用合理默认值,
    调用方可按需覆盖。返回的字符串可直接喂给 ``POST /experiments`` 的
    ``plan.content_md`` 字段或 SDK 的 ``PlanInput(content_md=...)``,不会触发
    STATE_MACHINE_PLAN_MARKER_MISSING。

    Args:
        title: 实验标题(必填 frontmatter 字段)。
        body: frontmatter 之后的 markdown 正文;**整段 plan content_md 都包含
            frontmatter + body**,调用方拿到的字符串应原样存读,例如:

            .. code-block:: python

                plan_md = make_valid_plan(body="## plan")
                create = client.post(..., json={"plan": {"content_md": plan_md}})
                # ...
                assert body["plans"][0]["content_md"] == plan_md  # 完整回环
        acceptance: 实验验收标准列表;默认 ``["acceptance-1"]``。
        evidence_keys: 证据键列表;默认 ``["pytest_summary"]``。
        dependencies: 依赖项 ID 列表;默认 ``["none"]``(plan_marker_service 拒绝
            空列表 ``PLAN_MARKER_EMPTY_LIST``,需要一个非空占位符)。

    Returns:
        含 ``---\\n...\\n---\\n<body>\\n`` 的完整 markdown。
    """
    acc = list(acceptance) if acceptance is not None else ["acceptance-1"]
    ev = list(evidence_keys) if evidence_keys is not None else ["pytest_summary"]
    dep = list(dependencies) if dependencies is not None else ["none"]

    lines = [
        "---",
        f"title: {title}",
        "acceptance:",
        *[f"  - {a}" for a in acc],
        "evidence_keys:",
        *[f"  - {e}" for e in ev],
        "dependencies:",
        *[f"  - {d}" for d in dep],
        "---",
        body,
    ]
    return "\n".join(lines) + "\n"
