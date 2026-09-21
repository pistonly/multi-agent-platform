"""``map experiment show``/``status``/``complete``/``log`` 默认精简视图（I4）。

A4：默认视图不再内联 plan ``content_md`` 全文 / log 全文回显——只给
plan_version_count + plan_file_path 指针 + 首行摘录 + 行数标注；
``--full`` 或显式 ``--format yaml`` 恢复全文（runner.human_renderer 只在
``format_source == "default"`` 且 yaml 时生效，机器契约不动）；
``--format json`` 逐字段不变。纯函数模块，不触网络。

兼容 pydantic model 与 dict 两种载荷（与 work_compact_view 同约定）。
"""

from __future__ import annotations

from typing import Any

_EXCERPT_MAX = 120
_DESC_MAX = 200
# content_md 行数不超过该阈值时视为 stub / 短计划，无需"不内联"提示行。
_INLINE_THRESHOLD = 12


def _get(obj: Any, key: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _short(value: Any) -> str:
    return str(value)[:8] if value is not None else ""


def _enum(value: Any) -> str | None:
    if value is None:
        return None
    return str(getattr(value, "value", value))


def _excerpt(text: Any, limit: int = _EXCERPT_MAX) -> str:
    s = str(text or "").replace("\n", " ").strip()
    if len(s) > limit:
        return s[: limit - 1] + "…"
    return s


def _plan_lines(exp: Any) -> list[str]:
    plan = _get(exp, "current_plan")
    if plan is None:
        return []
    version = _get(plan, "version")
    content = _get(plan, "content_md") or ""
    n_lines = len(content.splitlines())
    first = next((ln.strip() for ln in content.splitlines() if ln.strip()), "")
    path = _get(exp, "plan_file_path")
    head = f"plan: v{version}"
    if path:
        head += f" @ {path}"
    head += f" ({n_lines} lines)"
    lines = [head]
    if first:
        lines.append(f"  summary: {_excerpt(first)}")
    note = _get(plan, "change_note")
    if note:
        lines.append(f"  change_note: {_excerpt(note)}")
    if n_lines > _INLINE_THRESHOLD:
        lines.append(f"  (content_md elided — use --full or --format yaml to inline all {n_lines} lines)")
    return lines


def render_experiment_compact(exp: Any) -> str:
    """ExperimentDetailRead（model 或 dict）→ 精简人类可读多行文本。"""
    lines: list[str] = []
    head = f"experiment: {_excerpt(_get(exp, 'title'))} [{_enum(_get(exp, 'phase'))}]"
    mode = _enum(_get(exp, "mode"))
    if mode and mode != "standard":
        head += f" mode={mode}"
    # 完整 uuid 必须保留：shortid 工作流（list 短前缀 → show 取完整 uuid）
    # 依赖 show 输出（tests/test_cli_shortid.py），且它是后续命令的输入。
    eid = str(_get(exp, "id") or "").strip()
    if eid:
        head += f" id={eid}"
    lines.append(head)
    desc = _get(exp, "description")
    if desc:
        lines.append(f"  description: {_excerpt(desc, _DESC_MAX)}")
    lines.extend(_plan_lines(exp))
    count = _get(exp, "plan_version_count")
    lines.append(f"plan_versions: {count if count is not None else '?'}")
    review_count = _get(exp, "review_count")
    log_count = _get(exp, "log_count")
    counters = f"reviews: {review_count if review_count is not None else 0}  logs: {log_count if log_count is not None else 0}"
    lines.append(counters)
    latest = _get(exp, "latest_log_summary")
    if latest:
        lines.append(f"  latest: {_excerpt(latest)}")
    actions = _get(exp, "actions") or []
    if actions:
        lines.append(f"actions: {', '.join(str(a) for a in actions)}")
    blocked = _get(exp, "blocked_on")
    if blocked and blocked != "none":
        lines.append(f"blocked_on: {blocked}")
    owner = _enum(_get(exp, "phase_owner"))
    if owner and owner != "host":
        lines.append(f"phase_owner: {owner}")
    if _get(exp, "informational_only"):
        lines.append("informational_only: true")
    if _get(exp, "hidden_for_current_persona"):
        lines.append("hidden_for_current_persona: true")
    unreasonable = _get(exp, "open_unreasonable_count") or 0
    if unreasonable:
        lines.append(f"open_unreasonable_count: {unreasonable}")
    topic = _short(_get(exp, "topic_id"))
    if topic:
        lines.append(f"topic: {topic}")
    acceptance = _get(exp, "acceptance_status") or []
    if acceptance:
        lines.append(f"acceptance: {len(acceptance)} items")
    template = _get(exp, "template_validation")
    if template is not None:
        warns = _get(template, "warnings") or []
        if warns:
            lines.append(f"template_validation: {len(warns)} warnings (details on stderr)")
        else:
            lines.append("template_validation: ok")
    return "\n".join(lines)


def render_log_create_compact(resp: Any) -> str:
    """LogCreateResponse（model 或 dict）→ 精简回显（log 正文不内联）。"""
    lines: list[str] = []
    log = _get(resp, "log")
    head = "log: created"
    lid = _short(_get(log, "id") if log is not None else None)
    if lid:
        head += f" id={lid}"
    lines.append(head)
    summary = _get(log, "summary") if log is not None else None
    if summary:
        lines.append(f"  summary: {_excerpt(summary)}")
    path = _get(log, "file_path") if log is not None else None
    content = _get(log, "content_md") if log is not None else None
    n_lines = len((content or "").splitlines())
    if path:
        lines.append(f"  file: {path} (slim form)")
    else:
        lines.append(f"  content_md: {n_lines} lines stored (use --format yaml to re-echo)")
    validation = _get(resp, "validation")
    if validation is not None:
        warns = _get(validation, "warnings") or []
        if warns:
            missing = ", ".join(str(_get(w, "missing_key")) for w in warns)
            lines.append(f"  validation: {len(warns)} warnings (missing: {missing})")
        else:
            lines.append("  validation: ok")
    sim = _get(resp, "similarity_warning")
    if sim is not None:
        lines.append("  similarity: warning fired (details on stderr)")
    return "\n".join(lines)
