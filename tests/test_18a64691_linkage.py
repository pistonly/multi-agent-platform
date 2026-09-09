"""18a64691 联动断言 (experiment 8d52232d acceptance (g)).

18a64691 topic `[P2][UX] mark-seen help 文本 obligation 术语不够友好`
收敛在「全平台 `obligation` → `action item` / `待办` 双标 + migration 保留
alias」。本测试断言:

1. **18a64691 已闭环**: topic 状态 closed + decision 明确包含
   `obligation → action item` 替换 PR 范围(为 18a64691 维护条款,本测试
   只断言事实,不重做 PR)。
2. **(c) 字段重命名不破坏 18a64691 推进路径**: 本实验 (c) 改动覆盖的源
   文件(SDK / server / cli / web) **不含** 新引入的 `obligation` 字符串
   —— 既守住 18a64691 的全局术语替换,也不让本实验误用 18a64691 已宣告
   弃用的术语。
3. **(c) 字段重命名已就位**: 新字段名 `stale_since` / `visibility` 已
   在 SDK Pydantic schema + TS OpenAPI codegen 双向就位;旧字段名
   `advance_round_pending_since` / `partition_visibility` 保留为
   AliasChoices / computed_field 兼容层。
4. **(c part 2) CLI deprecation warning 已上线**: CLI 检测到旧字段名时
   会在 stderr 输出"已替代/已弃用"提示,与 18a64691 全平台 changelog
   联动。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.slow

LINKAGE_TOPIC_ID = "18a64691-a9b2-402d-b9f8-2df1593d7097"
REPO_ROOT = Path(__file__).resolve().parent.parent


def _show_topic(topic_id: str) -> dict | None:
    """Run `map topic show --id ...` and parse YAML output.

    联动断言依赖**本机 MAP 实例**里存在该话题（dogfood 环境）。话题已归档 /
    未同步的开发机或 CI 里 CLI 会 exit 1（not found / 410 retired）——返回
    None，由调用方 pytest.skip，避免把环境差异伪装成契约回归。
    """
    proc = subprocess.run(
        ["map", "--persona", "host", "topic", "show", "--id", topic_id],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return None
    # PyYAML round-trip via safe_load (yaml is a runtime dep of the CLI).
    import yaml

    return yaml.safe_load(proc.stdout)


def test_18a64691_topic_is_closed():
    """18a64691 must be closed — its decision defines the contract this
    experiment's (c) field rename must not regress."""
    topic = _show_topic(LINKAGE_TOPIC_ID)
    if topic is None:
        pytest.skip("18a64691 topic not present in this machine's MAP (archived / not synced)")
    assert topic["status"] == "closed", (
        f"18a64691 expected closed, got {topic['status']!r}; "
        "rerun this assertion after the linkage topic is re-opened"
    )


def test_18a64691_decision_documents_obligation_to_action_item_rename():
    """The decision text must explicitly mention `obligation → action item`.

    This pins the linkage so a future regression in 18a64691 (e.g. someone
    re-opening it and forgetting the rename scope) is caught here.
    """
    topic = _show_topic(LINKAGE_TOPIC_ID)
    if topic is None:
        pytest.skip("18a64691 topic not present in this machine's MAP (archived / not synced)")
    decision_text = topic["decision"]["decision"]
    assert "obligation" in decision_text, decision_text
    assert "action item" in decision_text, decision_text
    assert "待办" in decision_text, decision_text
    # Sanity: the linkage scope covers the 3 PR + 1 changelog deliverables.
    for needle in ("Glossary", "changelog"):
        assert needle in decision_text, f"missing linkage scope: {needle}"


def test_field_rename_files_do_not_introduce_obligation_term():
    """Files modified by this experiment's (c) field rename must not introduce
    fresh `obligation` references in the *new* code paths. This guards the
    18a64691 global rename contract — if (c) adds a new occurrence, the
    migration alias / deprecation warning path leaks the old term into the
    runtime surface.

    Scope: this scan only covers the files this experiment added or modified
    for the summary / rename work. Pre-existing `obligation` occurrences in
    `cli/main.py` and `topic_work_item_service.py` (priority="obligation"
    discriminator and the mark-seen help text) are 18a64691's own cleanup
    scope and intentionally not regressed here.
    """
    # Files this experiment introduced / rewrote for (c) field rename + summary.
    paths_to_scan = [
        "sdk/python/map_types/schemas",  # package dir (split from schemas.py)
        "server/services/agent_work_service.py",
        "web/src/api/client.ts",
        "web/src/api/types.ts",
        "web/src/components/WorkSummaryCard.tsx",
        "web/src/components/WorkSummaryCard.test.tsx",
    ]
    violations: list[tuple[str, int, str]] = []
    pattern = re.compile(r"\bobligation\b", re.IGNORECASE)
    for rel in paths_to_scan:
        fp = REPO_ROOT / rel
        if fp.is_dir():
            files = sorted(fp.glob("*.py"))
        elif fp.exists():
            files = [fp]
        else:
            continue
        for f in files:
            for lineno, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                if pattern.search(line):
                    violations.append((str(f.relative_to(REPO_ROOT)), lineno, line.strip()))
    assert not violations, (
        "18a64691 linkage broken: (c) field rename introduced fresh `obligation`:\n"
        + "\n".join(f"  {p}:{ln}: {text}" for p, ln, text in violations)
    )


def test_field_rename_alias_round_trips_in_sdk():
    """`stale_since` (new) and `advance_round_pending_since` (old) must both
    be accepted by the SDK Pydantic schema for the legacy migration window."""
    from map_types.enums import TopicDiscussionRound
    from map_types.schemas import PendingRoundAckTodoRead

    base = {
        "id": "00000000-0000-0000-0000-000000000001",
        "topic_id": "00000000-0000-0000-0000-000000000002",
        "topic_title": "demo",
        "discussion_round": TopicDiscussionRound.round1.value,
        "round_summary_count": 0,
        "summary_excerpt": "x",
        "updated_at": "2026-07-01T00:00:00Z",
        "stale_since": "2026-07-01T00:00:00Z",
    }
    new_obj = PendingRoundAckTodoRead.model_validate(base)
    assert new_obj.stale_since is not None

    legacy = dict(base)
    legacy.pop("stale_since")
    legacy["advance_round_pending_since"] = "2026-07-01T00:00:00Z"
    legacy_obj = PendingRoundAckTodoRead.model_validate(legacy)
    assert legacy_obj.stale_since is not None
    # computed_field re-exposes the old key on dump for clients still using it.
    dumped = legacy_obj.model_dump(by_alias=False)
    assert dumped.get("stale_since") is not None


def test_field_rename_alias_appears_in_generated_typescript():
    """The TS OpenAPI codegen output must surface BOTH the new `visibility`
    field and the legacy `partition_visibility` alias. Frontend code that
    has not migrated yet still type-checks.
    """
    generated = (REPO_ROOT / "web" / "src" / "api" / "types.generated.ts").read_text(
        encoding="utf-8"
    )
    # Find the SummaryBucket interface block and assert both keys are present.
    m = re.search(
        r"export interface SummaryBucket \{[^}]*\}",
        generated,
        re.DOTALL,
    )
    assert m is not None, "SummaryBucket interface not found in generated types"
    body = m.group(0)
    assert "visibility" in body
    assert "partition_visibility" in body


def test_cli_deprecation_warning_helper_detects_legacy_aliases():
    """The CLI helper used by `map work` to surface 18a64691 migration warnings
    must emit deterministic text for both renamed fields. This is the same
    code path the CLI walks on every `map work` invocation.
    """
    from cli.runner import detect_deprecated_aliases

    payload = {
        "todos": {
            "pending_round_acks": [
                {"advance_round_pending_since": "2026-07-01T00:00:00Z"},
            ],
            "buckets": [{"partition_visibility": "host_only"}],
        }
    }
    warnings = detect_deprecated_aliases(payload)
    assert warnings == [
        "Warning: 'advance_round_pending_since' is deprecated; use 'stale_since' instead.",
        "Warning: 'partition_visibility' is deprecated; use 'visibility' instead.",
    ]
