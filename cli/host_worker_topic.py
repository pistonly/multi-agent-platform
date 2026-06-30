from __future__ import annotations

import re
import uuid
from typing import Any

ROUND_SUMMARY_RE = re.compile(r"^##\s+Round\s+(\d+)\s+Summary\b", re.IGNORECASE | re.MULTILINE)
ACTIVE_EXPERIMENT_PHASES = {"draft", "review", "approved", "running"}


def _host_has_round_summary(comments: list[dict[str, Any]], host_id: str, *, round_n: int) -> bool:
    for comment in comments:
        if str(comment.get("author_agent_id")) != host_id:
            continue
        body = str(comment.get("body") or "")
        for match in ROUND_SUMMARY_RE.finditer(body):
            if int(match.group(1)) == round_n:
                return True
    return False


def _host_has_round_summary_in_body(body: str) -> bool:
    return bool(ROUND_SUMMARY_RE.search(body))


def _flatten_comments(comments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for comment in comments:
        flattened.append(comment)
        flattened.extend(_flatten_comments(comment.get("children") or []))
    return flattened


def _has_active_experiment(topic: dict[str, Any]) -> bool:
    for experiment in topic.get("experiments") or []:
        if experiment.get("archived_at") is not None:
            continue
        if experiment.get("phase") in ACTIVE_EXPERIMENT_PHASES:
            return True
    return False


def _topic_resolution_payload(topic: dict[str, Any], result: dict[str, Any]) -> dict[str, Any] | None:
    decision = _clean_text(result.get("decision"))
    no_decision_reason = _clean_text(result.get("no_decision_reason"))
    if not decision and not no_decision_reason:
        if not result.get("create_experiment"):
            return None
        title = topic.get("title") or topic.get("id")
        decision = f"将话题“{title}”转入关联实验，由实验计划继续验证和落地。"

    payload: dict[str, Any] = {}
    if decision:
        payload["decision"] = decision
    if rationale := _clean_text(result.get("rationale")):
        payload["rationale"] = rationale
    elif decision and result.get("create_experiment"):
        round_count = int(topic.get("round_summary_count") or 0)
        payload["rationale"] = f"话题已完成 {round_count} 次 Round Summary，并满足 host bridge 开实验门禁。"
    if rejected_options := _clean_text(result.get("rejected_options")):
        payload["rejected_options"] = rejected_options
    if open_questions := _clean_text(result.get("open_questions")):
        payload["open_questions"] = open_questions
    if no_decision_reason:
        payload["no_decision_reason"] = no_decision_reason

    payload["action_items"] = _normalize_action_items(result.get("action_items"))
    return payload


def _normalize_action_items(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []

    items: list[dict[str, Any]] = []
    optional_fields = ("description", "due_at")
    uuid_fields = ("owner_agent_id", "linked_experiment_id")
    for value in raw:
        if not isinstance(value, dict):
            continue
        title = _clean_text(value.get("title"))
        if not title:
            continue
        item: dict[str, Any] = {"title": title}
        for field in optional_fields:
            if text := _clean_text(value.get(field)):
                item[field] = text
        for field in uuid_fields:
            if text := _clean_uuid_text(value.get(field)):
                item[field] = text
        items.append(item)
    return items


def _clean_uuid_text(value: Any) -> str | None:
    text = _clean_text(value)
    if text is None:
        return None
    try:
        return str(uuid.UUID(text))
    except ValueError:
        return None


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _default_plan(topic: dict[str, Any]) -> str:
    comments = _flatten_comments(topic.get("comments") or [])
    topic_id = topic.get("id")
    title = topic.get("title") or topic_id
    description = topic.get("description") or "无"
    return f"""# {title}

## 来源话题

- topic_id: `{topic_id}`
- 评论数: {len(comments)}

## 背景

{description}

## 目标

- 将话题讨论中的共识转化为可执行实验任务。
- 验证讨论中仍需落地的关键假设。

## 执行步骤

1. 整理话题中的共识、争议和约束。
2. 设计最小可验证变更或实验动作。
3. 执行实验并记录日志、结果和风险。
4. 根据实验结果更新项目状态或回到话题继续讨论。

## 验收标准

- 实验日志说明执行内容、结果、结论和后续动作。
- 若发现阻塞，日志中明确阻塞原因和需要的下一步输入。
"""
