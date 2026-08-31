"""T7 server 侧 _view_as_fs_topic experiments 注入回归测试 (实验 e6d23886 I11)。

2 case（与 plan §I11 验收对齐）：
- test_remote_close_nonterminal_experiment_rejected:
  关联实验 phase=running → close 第 4 维门禁真正拒绝（验证 I10 注入生效）
- test_remote_close_terminal_experiment_allowed:
  关联实验 phase=done → close 第 4 维门禁放行

实现策略（plan §I11 轻量路径）：直接调 `_view_as_fs_topic` + `validate_close`
组合（不走 FastAPI + DB session），断言 ``OpenExperimentError`` / 成功。
该组合就是 server remote close 真实路径（validate_fs_close 内层调用
``validate_close(_view_as_fs_topic(view), ...)``），因此纯单测即可覆盖
D4 门禁在 server 侧的真实生效情况。

parity 验证：
- pre-fix (无 I10)：_view_as_fs_topic 返回的 FsTopic.experiments 为空 list，
  validate_close 找不到 non-terminal 实验，第 4 维门禁误放行 → 用例失败
- post-fix (有 I10)：透传 view.experiments，validate_close 命中 non-terminal
  → 用例通过
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from map_fs import (
    FsExperiment,
    OpenExperimentError,
    validate_close,
)

from server.services.fs_source_service import (
    _TopicView,
    _view_as_fs_topic,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_view(*, exp_phase: str) -> _TopicView:
    """构造 _TopicView，关联一个 phase=exp_phase 的实验。"""
    slug = "test-topic"
    now = datetime.now(timezone.utc)
    return _TopicView(
        slug=slug,
        id=uuid.uuid4(),
        title="Test Topic",
        description="",
        status="open",
        round="2",
        round_number=2,
        creator="multi-agent-platform-host",
        created_at=now,
        updated_at=now,
        dir_path="map/topics/test-topic",
        experiments=[
            FsExperiment(
                slug="test-exp",
                id=uuid.uuid4(),
                title="Test Exp",
                description="",
                phase=exp_phase,
                creator="multi-agent-platform-host",
                created_at=now,
                dir_path="map/experiments/test-exp",
                topic=slug,
            ),
        ],
    )


# ---------------------------------------------------------------------------
# I11 case 1：非 terminal 实验必须拒绝 close
# ---------------------------------------------------------------------------


def test_remote_close_nonterminal_experiment_rejected() -> None:
    """关联实验 phase=running → close 第 4 维门禁真正拒绝（I10 注入生效）。

    与 plan §I11 parity 验证：
    - pre-fix：_view_as_fs_topic 不透传 experiments → FsTopic.experiments=[]
      → validate_close 找不到 non-terminal → 误放行 → 本断言 fail
    - post-fix：experiments 透传 → validate_close 命中 running → 拒绝
    """
    view = _make_view(exp_phase="running")

    # I10 注入核心断言：_view_as_fs_topic 透传 experiments 字段
    fs_topic = _view_as_fs_topic(view)
    assert len(fs_topic.experiments) == 1, (
        "I10 修复目标：_view_as_fs_topic 必须透传关联实验列表，否则 server "
        "remote close 第 4 维门禁失效（experiments=空 → 误判无 active 实验）"
    )
    assert fs_topic.experiments[0].phase == "running"

    # D4 门禁真正生效：validate_close 拒绝 non-terminal 实验
    with pytest.raises(OpenExperimentError) as exc_info:
        validate_close(fs_topic, close_reason="experiment_done")
    assert "running" in str(exc_info.value)


# ---------------------------------------------------------------------------
# I11 case 2：terminal 实验 close 放行
# ---------------------------------------------------------------------------


def test_remote_close_terminal_experiment_allowed() -> None:
    """关联实验 phase=done → close 第 4 维门禁放行。

    镜像 case 1：确保 I10 注入未引入「过度严格」回归（terminal 实验仍可关闭）。
    """
    view = _make_view(exp_phase="done")

    fs_topic = _view_as_fs_topic(view)
    assert len(fs_topic.experiments) == 1
    assert fs_topic.experiments[0].phase == "done"

    fields = validate_close(fs_topic, close_reason="experiment_done")
    assert fields["status"] == "closed"
    assert fields["close_reason"] == "experiment_done"


# ---------------------------------------------------------------------------
# I11 case 3：cancelled 也属于 terminal — 镜像校验
# ---------------------------------------------------------------------------


def test_remote_close_cancelled_experiment_allowed() -> None:
    """关联实验 phase=cancelled → close 放行（cancelled 视为 terminal）。"""
    view = _make_view(exp_phase="cancelled")

    fs_topic = _view_as_fs_topic(view)
    assert fs_topic.experiments[0].phase == "cancelled"

    fields = validate_close(fs_topic, close_reason="cancelled")
    assert fields["status"] == "closed"
    assert fields["close_reason"] == "cancelled"
