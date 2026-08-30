"""notification_fanout 单测（实验 8b1d20a1 I4 — plan A2/A4 验收）。

覆盖：

1. **obligation 豁免**：experiment.lifecycle.cancelled / withdrawn /
   review_item.status_changed / experiment.phase_changed(review/result_review)
   → 全量 fan-out 给 reviewer（reviewer 永远在内）
2. **topic.* contextual**：按白名单过滤——reviewer 等旁观者被滤掉
3. **白名单解析失败**：保守放行（保留全量 fan-out）
4. **其它 event**：不过滤（维持现有行为）
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from server.services.notification_fanout import (
    _is_obligation_exempt,
    _is_topic_event,
    filter_recipients,
)

# ---------------------------------------------------------------------------
# 纯函数单元测试
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "event",
    [
        "experiment.lifecycle.cancelled",
        "experiment.lifecycle.withdrawn",
        "review_item.status_changed",
    ],
)
def test_obligation_exempt_static_set(event: str) -> None:
    """reviewer round2 硬边界：列举 event 永远全量 fan-out。"""
    assert _is_obligation_exempt(event, payload=None) is True


@pytest.mark.parametrize(
    "phase",
    ["review", "result_review"],
)
def test_experiment_phase_changed_obligation_when_phase_obligation(phase: str) -> None:
    """experiment.phase_changed 仅 phase ∈ {review, result_review} 时 obligation 豁免。"""
    assert (
        _is_obligation_exempt(
            "experiment.phase_changed", payload={"phase": phase}
        )
        is True
    )


@pytest.mark.parametrize(
    "phase",
    ["draft", "approved", "running", "done"],
)
def test_experiment_phase_changed_not_obligation_when_phase_other(phase: str) -> None:
    """experiment.phase_changed 其它阶段不豁免——按 topic.* 同口径走白名单。"""
    assert (
        _is_obligation_exempt(
            "experiment.phase_changed", payload={"phase": phase}
        )
        is False
    )


@pytest.mark.parametrize(
    "event",
    [
        "topic.comment.created",
        "topic.round_advanced",
        "topic.lifecycle.closed",
        "topic.close_pending",
    ],
)
def test_topic_event_detected(event: str) -> None:
    assert _is_topic_event(event) is True


# ---------------------------------------------------------------------------
# filter_recipients 行为分支（mock topic_role_resolver）
# ---------------------------------------------------------------------------


def _make_agent(name: str, project_id: uuid.UUID | None = None):
    """构造 Agent stub；filter_recipients 只读 ``agent.name``。"""
    return SimpleNamespace(
        id=uuid.uuid4(),
        name=name,
        project_id=project_id,
    )


def _project_stub() -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4())


def test_obligation_exempt_event_bypasses_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """obligation 豁免 event → 全量放行（reviewer 在内）。"""
    project = _project_stub()
    reviewer = _make_agent("reviewer")
    host = _make_agent("host")

    # 即使白名单仅含 host，obligation 豁免 event 也保留 reviewer
    monkeypatch.setattr(
        "server.services.topic_role_resolver.resolve_topic_whitelist",
        lambda *_a, **_kw: {"host"},
    )
    result = filter_recipients(
        SimpleNamespace(),
        project=project,
        event="experiment.lifecycle.cancelled",
        target_type="experiment",
        target_id=uuid.uuid4(),
        payload=None,
        recipients=[host, reviewer],
    )
    assert set(a.name for a in result) == {"host", "reviewer"}


def test_topic_event_filters_by_whitelist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """topic.* event → 按白名单过滤；非白名单角色被剔除。"""
    project = _project_stub()
    host = _make_agent("host")
    participant = _make_agent("participant")
    reviewer = _make_agent("reviewer")  # 旁观者，应被滤掉

    monkeypatch.setattr(
        "server.services.topic_role_resolver.resolve_topic_whitelist",
        lambda *_a, **_kw: {"host", "participant"},
    )
    result = filter_recipients(
        SimpleNamespace(),
        project=project,
        event="topic.lifecycle.closed",
        target_type="topic",
        target_id=uuid.uuid4(),
        payload={"close_note": "x"},
        recipients=[host, participant, reviewer],
    )
    names = set(a.name for a in result)
    assert names == {"host", "participant"}
    assert "reviewer" not in names


def test_topic_event_conservative_passthrough_on_whitelist_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """白名单解析失败 → 保守放行（保留 reviewer）。"""
    project = _project_stub()
    host = _make_agent("host")
    reviewer = _make_agent("reviewer")

    # resolve_topic_whitelist 失败路径：返回 None
    monkeypatch.setattr(
        "server.services.topic_role_resolver.resolve_topic_whitelist",
        lambda *_a, **_kw: None,
    )
    result = filter_recipients(
        SimpleNamespace(),
        project=project,
        event="topic.lifecycle.closed",
        target_type="topic",
        target_id=uuid.uuid4(),
        payload=None,
        recipients=[host, reviewer],
    )
    assert set(a.name for a in result) == {"host", "reviewer"}


def test_non_topic_non_obligation_event_passes_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """非 topic.* + 非 obligation 豁免 → 不过滤（维持现有行为）。

    例如 system.runtime_attention（默认 obligation 集合外）/ action_item.* 等。
    """
    project = _project_stub()
    reviewer = _make_agent("reviewer")
    host = _make_agent("host")

    # 即便 resolve_topic_whitelist 抛异常也不该被调用（非 topic.* 不解析）
    def _should_not_be_called(*_a, **_kw):
        raise AssertionError("non-topic event should not invoke resolver")

    monkeypatch.setattr(
        "server.services.topic_role_resolver.resolve_topic_whitelist",
        _should_not_be_called,
    )
    result = filter_recipients(
        SimpleNamespace(),
        project=project,
        event="system.runtime_attention",
        target_type="system",
        target_id=None,
        payload=None,
        recipients=[host, reviewer],
    )
    assert set(a.name for a in result) == {"host", "reviewer"}


def test_topic_event_with_non_topic_target_type_skips_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """target_type != "topic" 即便 event 是 topic.* 也跳过白名单。

    plan A2 范围仅约束 target_type=topic 的事件；其它 target_type 的
    topic.* event（如 topic_comment 事件桥）的角色过滤留待后续实验。
    """
    project = _project_stub()
    reviewer = _make_agent("reviewer")
    host = _make_agent("host")

    def _should_not_be_called(*_a, **_kw):
        raise AssertionError("non-topic target_type should not invoke resolver")

    monkeypatch.setattr(
        "server.services.topic_role_resolver.resolve_topic_whitelist",
        _should_not_be_called,
    )
    result = filter_recipients(
        SimpleNamespace(),
        project=project,
        event="topic.lifecycle.closed",
        target_type="topic_comment",
        target_id=uuid.uuid4(),
        payload=None,
        recipients=[host, reviewer],
    )
    assert set(a.name for a in result) == {"host", "reviewer"}


def test_empty_recipients_returns_empty() -> None:
    """recipients 为空 → 直接返回空列表（不触发白名单解析）。"""
    project = _project_stub()
    result = filter_recipients(
        SimpleNamespace(),
        project=project,
        event="topic.lifecycle.closed",
        target_type="topic",
        target_id=uuid.uuid4(),
        payload=None,
        recipients=[],
    )
    assert result == []


def test_filter_preserves_recipient_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """filter 保留 recipients 输入顺序（稳定排序，便于审计/调试）。"""
    project = _project_stub()
    a = _make_agent("host")
    b = _make_agent("host")  # 重复名仅用于顺序断言
    c = _make_agent("participant")
    # _make_agent 都给不同 uuid，所以是不同 Agent 对象
    monkeypatch.setattr(
        "server.services.topic_role_resolver.resolve_topic_whitelist",
        lambda *_a, **_kw: {"host", "participant"},
    )
    result = filter_recipients(
        SimpleNamespace(),
        project=project,
        event="topic.lifecycle.closed",
        target_type="topic",
        target_id=uuid.uuid4(),
        payload=None,
        recipients=[a, b, c],
    )
    assert [r.id for r in result] == [a.id, b.id, c.id]
