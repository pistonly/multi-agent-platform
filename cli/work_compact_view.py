"""``map work`` 默认人类可读精简视图（map exp 4e4206de I1）。

实验 token-quickwins-and-measurement A1：默认视图只保留 Agent 决策需要的
字段——agent 身份块（A2 的 work 内身份确认依据）、非空 todos 分区精简
条目、通知单行摘要；空分区折叠为一行；lock/source/timestamps 等诊断
字段全部退到 ``--verbose``（完整 YAML）与 ``--format json``（机器契约，
逐字段不变）。纯函数模块，不触网络。

兼容 pydantic model 与 dict 两种载荷（``_run`` 的 action 结果两者皆有：
SDK 返回 model，mock/直连路径可能返回 dict）。

尺寸判据（A1 验收）：固定状态夹具（空 todos 分区 + 空通知 + 固定 agent
块）渲染输出 ≤800B（tests/test_cli_work_compact_view.py）。
"""

from __future__ import annotations

from typing import Any

_EXCERPT_MAX = 80

_TODO_SECTIONS = (
    "my_open_experiments",
    "executor_assignments",
    "pending_reviews",
    "pending_result_reviews",
    "experiment_review_informational",
    "pending_replies",
    "pending_plan_revisions",
    "pending_topic_replies",
    "pending_round_acks",
    "pending_advance_rounds",
    "stale_open_topics",
    "my_open_topics",
    "mentions",
    "action_items",
)


def _get(obj: Any, key: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _short(value: Any) -> str:
    return str(value)[:8] if value is not None else ""


def _enum(value: Any) -> str:
    """枚举取 ``value`` 再转字符串。

    Python ≥3.11 起 str-mixin 枚举的 ``str()``/f-string 会带上类名前缀
    （``NotificationCategory.digest`` 而非 ``digest``），而 3.10 不带——
    不显式取值会让默认视图跨版本输出不一致、且在 3.11+ 上凭空变长。
    与 ``cli/experiment_compact_view._enum`` 同款处理。
    """
    return str(getattr(value, "value", value))


def _first(obj: Any, *names: str) -> Any:
    for name in names:
        value = _get(obj, name)
        if value is not None:
            return value
    return None


def _items(obj: Any) -> list[Any]:
    value = _get(obj, "items")
    return list(value) if value else []


def _excerpt(text: Any) -> str:
    s = str(text or "").replace("\n", " ").strip()
    if len(s) > _EXCERPT_MAX:
        return s[: _EXCERPT_MAX - 1] + "…"
    return s


def _render_agent(agent: Any) -> list[str]:
    bits = [f"agent: {_get(agent, 'name') or '?'}"]
    role = _get(agent, "role")
    if role is not None:
        bits.append(f"({_enum(role)})")
    project = _get(agent, "project_key") or _get(agent, "project_id")
    if project is not None:
        bits.append(f"project={project}")
    sid = _short(_get(agent, "id"))
    if sid:
        bits.append(f"id={sid}")
    return [" ".join(bits)]


def _render_topic_progress(progress: Any) -> list[str]:
    items = _items(progress)
    if not items:
        return ["topic_progress: 0 topics"]
    lines = [f"topic_progress: {len(items)} topics"]
    for it in items:
        lines.append(
            f"  - {_excerpt(_get(it, 'topic_title'))} "
            f"round={_get(it, 'discussion_round')} new={_get(it, 'new_comment_count')}"
        )
        for wi in _get(it, "work_items") or []:
            if _get(wi, "priority") == "obligation":
                lines.append(f"      ! {_get(wi, 'kind')}: {_excerpt(_get(wi, 'reason'))}")
    return lines


def _render_todo_entry(entry: Any) -> list[str]:
    oid = _short(
        _first(entry, "id", "item_id", "experiment_id", "topic_id", "comment_id", "decision_id")
    )
    head = "    - "
    if oid:
        head += f"{oid} "
    phase = _first(entry, "phase", "status")
    if phase is not None:
        head += f"[{_enum(phase)}] "
    head += _excerpt(_first(entry, "experiment_title", "topic_title", "title"))
    author = _first(entry, "author_name", "review_progress")
    if author:
        head += f" — {author}"
    lines = [head]
    for field, label in (
        ("plan_file_path", "plan"),
        ("log_file_path", "log"),
        ("file_path", "file"),
    ):
        value = _first(entry, field)
        if value:
            lines.append(f"      {label}: {value}")
    round_ = _first(entry, "discussion_round")
    if round_ is not None:
        lines.append(f"      round: {round_}")
    blocked_on = _first(entry, "blocked_on")
    if blocked_on and blocked_on != "none":
        lines.append(f"      blocked_on: {blocked_on}")
    actions = _first(entry, "actions")
    if actions:
        lines.append(f"      actions: {', '.join(str(a) for a in actions)}")
    excerpt = _first(entry, "excerpt", "summary_excerpt")
    if excerpt:
        lines.append(f"      excerpt: {_excerpt(excerpt)}")
    return lines


def _render_todos(todos: Any) -> list[str]:
    sections = [(name, _get(todos, name) or []) for name in _TODO_SECTIONS]
    nonempty = [(n, list(v)) for n, v in sections if v]
    empty = [n for n, v in sections if not v]
    lines = ["todos:"]
    for name, entries in nonempty:
        lines.append(f"  {name}: {len(entries)}")
        for entry in entries:
            lines.extend(_render_todo_entry(entry))
    if empty:
        lines.append(f"  (empty: {', '.join(empty)})")
    return lines


def _render_notifications(notifications: Any) -> list[str]:
    if notifications is None:
        return []
    items = _items(notifications)
    unread = _get(notifications, "unread_count")
    if not items:
        return [f"notifications: {unread if unread is not None else 0} unread"]
    lines = [f"notifications: {unread if unread is not None else len(items)} unread"]
    for n in items:
        category = _get(n, "category") or "-"
        event = _get(n, "event") or "-"
        summary = _excerpt(_get(n, "summary"))
        target_type = _get(n, "target_type")
        target = f" -> {target_type} {_short(_get(n, 'target_id'))}".rstrip() if target_type else ""
        lines.append(f"  - [{_enum(category)}] {event} — {summary}{target}")
    return lines


def _render_source(source: Any) -> list[str]:
    if source is None:
        return []
    lines = [f"source: {_get(source, 'content_source') or '?'}"]
    if _get(source, "stale"):
        lines.append(f"  [WARN] source stale: {_get(source, 'stale_reason') or 'unknown'}")
    return lines


def render_work_compact(work: Any) -> str:
    """AgentWorkRead（model 或 dict）→ 精简人类可读多行文本（stdout）。"""
    lines: list[str] = []
    lines.extend(_render_agent(_get(work, "agent")))
    lines.extend(_render_topic_progress(_get(work, "topic_progress")))
    lines.extend(_render_todos(_get(work, "todos")))
    lines.extend(_render_notifications(_get(work, "notifications")))
    lines.extend(_render_source(_get(work, "source")))
    return "\n".join(lines)
