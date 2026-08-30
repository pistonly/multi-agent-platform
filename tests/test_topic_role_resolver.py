"""topic_role_resolver 单测（实验 8b1d20a1 I4 — plan A1 验收）。

仅覆盖 ``compute_whitelist_from_view`` 纯函数 + ``resolve_topic_whitelist``
的失败回退路径（mock fs_source_service.plane_views）。

fs_source_service.plane_views 自身不在本测试范围内（既有 test_fs_source.py
覆盖）；本测试关注"白名单派生口径 + 不可达回退"。
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from server.services.topic_role_resolver import (
    compute_whitelist_from_view,
    resolve_topic_whitelist,
)

# ---------------------------------------------------------------------------
# Fake _TopicView（避免依赖 fs_source_service 内部 dataclass）
# ---------------------------------------------------------------------------


def _make_view(
    *,
    participants: list[str],
    round_number: int,
    speakers: dict[int, list[str]] | None = None,
) -> SimpleNamespace:
    """Build a minimal view-like object exposing ``participants`` /
    ``round_number`` / ``authors_in_round``. duck typing 让本测试不依赖
    fs_source_service 内部 _TopicView。
    """
    speakers = speakers or {}

    def authors_in_round(n: int) -> set[str]:
        return set(speakers.get(n, []))

    return SimpleNamespace(
        participants=participants,
        round_number=round_number,
        authors_in_round=authors_in_round,
    )


# ---------------------------------------------------------------------------
# compute_whitelist_from_view
# ---------------------------------------------------------------------------


def test_whitelist_includes_all_participants_and_current_speakers() -> None:
    """creator ∪ declared ∪ speakers_current_round 全在内。"""
    view = _make_view(
        participants=["host", "participant"],
        round_number=2,
        speakers={2: ["participant"]},
    )
    whitelist = compute_whitelist_from_view(view)
    assert whitelist == {"host", "participant"}


def test_whitelist_includes_prev_round_speakers_for_continuity() -> None:
    """A1：prev_round 发言人也进白名单（unread_change 接力不断）。"""
    view = _make_view(
        participants=["host", "participant"],
        round_number=2,
        speakers={1: ["ghost_speaker"], 2: ["participant"]},
    )
    whitelist = compute_whitelist_from_view(view)
    assert "ghost_speaker" in whitelist  # 上一轮发言


def test_round1_has_no_prev_speakers() -> None:
    """round1 时 prev_round=0，authors_in_round(0) 返回空。"""
    view = _make_view(
        participants=["host", "participant"],
        round_number=1,
        speakers={1: ["participant"]},
    )
    whitelist = compute_whitelist_from_view(view)
    assert whitelist == {"host", "participant"}


def test_undeclared_but_engaged_speaker_lands_in_whitelist() -> None:
    """participant round1 护栏：未 declared 但本轮发言进入白名单。"""
    view = _make_view(
        participants=["host", "participant"],
        round_number=2,
        speakers={2: ["interloper"]},
    )
    whitelist = compute_whitelist_from_view(view)
    assert "interloper" in whitelist


def test_whitelist_dedup() -> None:
    """creator / declared / speakers 三集合并集去重。"""
    view = _make_view(
        participants=["host", "participant"],
        round_number=2,
        speakers={1: ["host"], 2: ["host", "participant"]},
    )
    whitelist = compute_whitelist_from_view(view)
    assert whitelist == {"host", "participant"}


def test_reviewer_not_in_whitelist_as_outsider() -> None:
    """reviewer 不在任何参与者集合——filter 应把 reviewer 当外来者。

    这是 reviewer 空唤醒根因的核心：A2 让 reviewer 在 topic.* 上不收
    wakeable，从而根治反复空唤醒。
    """
    view = _make_view(
        participants=["host", "participant"],
        round_number=2,
    )
    whitelist = compute_whitelist_from_view(view)
    assert "reviewer" not in whitelist


def test_empty_participants_returns_none() -> None:
    """participants 为空 → 返回 None（filter 层保守放行）。"""
    view = _make_view(participants=[], round_number=1)
    assert compute_whitelist_from_view(view) is None


# ---------------------------------------------------------------------------
# resolve_topic_whitelist — 失败回退
# ---------------------------------------------------------------------------


def _make_db_session_stub() -> SimpleNamespace:
    """Minimal DB stub — resolve_topic_whitelist 通过 plane_views 取数据，
    本测试不依赖具体 ORM。
    """
    return SimpleNamespace()


def test_resolve_returns_none_when_topic_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """plane_views 返回的列表里没目标 topic → None（保守放行）。"""
    project = SimpleNamespace(id=uuid.uuid4(), workspace_path="/nonexistent")
    topic_id = uuid.uuid4()
    fake_view = _make_view(
        participants=["host", "participant"], round_number=1
    )
    fake_view.id = uuid.uuid4()  # 与 topic_id 不一致

    def _fake_plane_views(_db, _project):
        return [fake_view]

    monkeypatch.setattr(
        "server.services.fs_source_service.plane_views", _fake_plane_views
    )
    result = resolve_topic_whitelist(
        _make_db_session_stub(), project=project, topic_id=topic_id
    )
    assert result is None


def test_resolve_returns_none_when_plane_views_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """plane_views 抛异常 → 返回 None（不阻塞通知主路径）。"""
    project = SimpleNamespace(id=uuid.uuid4())

    def _raise(_db, _project):
        raise RuntimeError("FS plane unavailable")

    monkeypatch.setattr(
        "server.services.fs_source_service.plane_views", _raise
    )
    result = resolve_topic_whitelist(
        _make_db_session_stub(), project=project, topic_id=uuid.uuid4()
    )
    assert result is None


def test_resolve_returns_whitelist_when_topic_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """happy path：plane_views 含目标 topic → 返回派生白名单。"""
    project = SimpleNamespace(id=uuid.uuid4())
    target_id = uuid.uuid4()
    fake_view = _make_view(
        participants=["host", "participant"],
        round_number=2,
        speakers={2: ["interloper"]},
    )
    fake_view.id = target_id

    def _fake_plane_views(_db, _project):
        return [fake_view]

    monkeypatch.setattr(
        "server.services.fs_source_service.plane_views", _fake_plane_views
    )
    result = resolve_topic_whitelist(
        _make_db_session_stub(), project=project, topic_id=target_id
    )
    assert result is not None
    assert "host" in result
    assert "participant" in result
    assert "interloper" in result
    assert "reviewer" not in result


# ---------------------------------------------------------------------------
# 当前不消费 current_round 参数；保留参数仅作 IO 优化钩子
# ---------------------------------------------------------------------------


def test_signature_accepts_current_round_kwarg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """resolve_topic_whitelist 暴露 current_round 钩子；当前实现忽略它。

    保证后续实验若引入 round 缓存时不必破坏调用方接口。
    """
    project = SimpleNamespace(id=uuid.uuid4())
    target_id = uuid.uuid4()
    fake_view = _make_view(participants=["host"], round_number=3)
    fake_view.id = target_id
    monkeypatch.setattr(
        "server.services.fs_source_service.plane_views",
        lambda _db, _project: [fake_view],
    )
    # 不传 current_round + 传 current_round 都应正常返回
    r1 = resolve_topic_whitelist(
        _make_db_session_stub(), project=project, topic_id=target_id
    )
    assert r1 == {"host"}
