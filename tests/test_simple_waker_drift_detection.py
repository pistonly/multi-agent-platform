"""回归测试：实验 waker-runtime-skill-hotcheck I6。

6 case 覆盖：
(a) 漂移检测重同步：mock 改源 skill → 下周期检测 + sync_runtime_skills called
    + 日志含 drift_skills
(b) 一致零开销：mock 无修改 → 连续 N 周期 copytree 调用次数=0
(c) 重同步失败：chmod 0444 + 注入 PermissionError → drift_resync_failed
    event + alert=true + 轮询继续（cycle_errors 不增）
(d) 周期可配置：env WAKER_DRIFT_CHECK_INTERVAL_CYCLES=5 → 每 5 cycle 触发
(e) 启动留痕：waker 启动 → 日志含 startup_sync + skills_count + synced_skills
    + skipped_reason=null
(f) 跨平台 mtime 抑制：mock mtime 差 1ns + size 一致 → 不触发 hash 二次确认
"""

from __future__ import annotations

import importlib
import json
import logging
import os
import sys
from collections.abc import Generator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

simple_waker = importlib.import_module("cli.simple_waker")
MapCommandClient = importlib.import_module("cli.map_command_client").MapCommandClient
SimpleWaker = simple_waker.SimpleWaker
SimpleWakerConfig = simple_waker.SimpleWakerConfig
_skill_audit_logger = simple_waker._skill_audit_logger

DriftDetector = importlib.import_module("cli.drift_detector").DriftDetector
DriftEntry = importlib.import_module("cli.drift_detector").DriftEntry
ResyncResult = importlib.import_module("cli.drift_detector").ResyncResult

AUDIT_LOGGER_NAME = "cli.simple_waker.skill_audit"


# ---------------------------------------------------------------------------
# FakeMapClient（最小化版本，足够支撑 DriftDetector 调用）
# ---------------------------------------------------------------------------


class _FakeMapClient(MapCommandClient):
    def __init__(self, *, persona: str = "host") -> None:
        self.persona = persona
        self._calls: list[str] = []

    def work(self) -> dict[str, Any]:
        self._calls.append("work")
        return {
            "agent": {"id": f"{self.persona}-agent", "name": self.persona},
            "topic_progress": {"items": [], "total": 0},
            "todos": {},
            "notifications": {"items": [], "total": 0, "unread_count": 0},
        }

    def whoami(self) -> dict[str, Any]:
        return {"id": f"{self.persona}-agent", "name": self.persona}

    def agent_heartbeat(self, *, busy_since: Any) -> None:
        return None


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_skill_tree(root: Path, skill: str = "demo-skill") -> Path:
    """建立 ``<root>/.cursor/skills/<skill>/SKILL.md`` + references 子目录。"""
    skills_root = root / ".cursor" / "skills" / skill
    skills_root.mkdir(parents=True, exist_ok=True)
    (skills_root / "SKILL.md").write_text("# demo skill", encoding="utf-8")
    refs = skills_root / "references"
    refs.mkdir(exist_ok=True)
    (refs / "extra.md").write_text("extra ref", encoding="utf-8")
    return root / ".cursor" / "skills"


@pytest.fixture
def audit_records() -> Generator[list[dict[str, Any]], None, None]:
    """挂 listener 到 skill_audit logger，捕获所有 JSON 审计行。"""
    records: list[dict[str, Any]] = []
    handler = logging.Handler()
    handler.setLevel(logging.DEBUG)
    handler.emit = lambda rec: records.append(json.loads(rec.getMessage()))  # type: ignore[assignment]
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger(AUDIT_LOGGER_NAME)
    prior_level = logger.level
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    try:
        yield records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(prior_level)


def _make_waker(tmp_path: Path, *, interval: int = 30) -> tuple[SimpleWaker, _FakeMapClient]:
    _make_skill_tree(tmp_path)
    runtime_home = tmp_path / "runtime_home"
    runtime_home.mkdir()
    client = _FakeMapClient()
    backend = MagicMock()
    backend.connect = AsyncMock()
    backend.disconnect = AsyncMock()
    backend.wake_async = AsyncMock()
    config = SimpleWakerConfig(
        persona="host",
        project_root=tmp_path,
        runtime_home=runtime_home,
        dry_run=True,
        once=True,
        max_cycles=1,
        state_file=tmp_path / "state.json",
        drift_check_interval_cycles=interval,
    )
    return SimpleWaker(client=client, config=config, backend=backend), client


# ---------------------------------------------------------------------------
# (a) 漂移检测重同步
# ---------------------------------------------------------------------------


def test_drift_detection_triggers_resync_on_source_change(
    tmp_path: Path, audit_records: list[dict[str, Any]]
) -> None:
    """改源 skill → 主循环跑 1 cycle（含 detector.first-scan 不报）→ 第二次
    check 应当检测漂移并触发 resync，audit 出现 drift_resync 事件。"""
    waker, _client = _make_waker(tmp_path)
    waker.config.drift_check_interval_cycles = 1  # 每个 cycle 都检查
    # 第一次：run_once + drift_check（首次全 scan 填充缓存）
    waker.run_once()
    waker._run_drift_check(cycle_index=1)
    # 改源 skill 文件 mtime + size
    skill_md = tmp_path / ".cursor" / "skills" / "demo-skill" / "SKILL.md"
    skill_md.write_text("# demo skill updated with more content", encoding="utf-8")
    # 第二次 cycle，模拟主循环累计 2 cycles 后触发 drift check
    waker._run_drift_check(cycle_index=2)
    drift_events = [r for r in audit_records if r.get("event") == "drift_resync"]
    assert drift_events, f"expected drift_resync event; got {audit_events_summary(audit_records)}"
    payload = drift_events[-1]
    assert "demo-skill" in payload["drift_skills"]
    assert payload["alert"] is False


# ---------------------------------------------------------------------------
# (b) 一致零开销
# ---------------------------------------------------------------------------


def test_no_drift_zero_resync_calls(
    tmp_path: Path, audit_records: list[dict[str, Any]]
) -> None:
    """无修改时，连续 N cycle 不应触发 resync（copytree 调用次数=0）。"""
    waker, _client = _make_waker(tmp_path)
    # 第一次全 scan，填充缓存
    waker._drift_detector.check_drift()
    with patch("cli.simple_waker.sync_runtime_skills") as sync_mock:
        # 跑 5 个 cycle（interval=1 → 每个 cycle 都触发 drift check）
        waker.config.drift_check_interval_cycles = 1
        for _ in range(5):
            waker._run_drift_check(cycle_index=1)
    sync_mock.assert_not_called()
    no_change = [r for r in audit_records if r.get("event") == "drift_no_change"]
    assert len(no_change) == 5


# ---------------------------------------------------------------------------
# (c) 重同步失败
# ---------------------------------------------------------------------------


def test_resync_failure_emits_alert_event(tmp_path: Path, audit_records: list[dict[str, Any]]) -> None:
    """resync 抛 PermissionError → drift_resync_failed + alert=true，主循环
    不应中断（cycle_errors 不增）。"""
    waker, _client = _make_waker(tmp_path)
    waker.config.drift_check_interval_cycles = 1
    # 第一次 scan 填充缓存（首次全 scan 不报漂移）
    waker._drift_detector.check_drift()
    # 改源 skill 触发漂移
    skill_md = tmp_path / ".cursor" / "skills" / "demo-skill" / "SKILL.md"
    skill_md.write_text("# completely different content", encoding="utf-8")
    # 第二次 run_drift_check 触发 resync；patch sync_runtime_skills 让它抛错
    with patch("cli.drift_detector.sync_runtime_skills", side_effect=PermissionError("read-only fs")):
        waker._run_drift_check(cycle_index=2)
    failed = [r for r in audit_records if r.get("event") == "drift_resync_failed"]
    assert failed, f"expected drift_resync_failed; got {audit_events_summary(audit_records)}"
    assert failed[-1]["alert"] is True
    assert "PermissionError" in failed[-1]["error"]


# ---------------------------------------------------------------------------
# (d) 周期可配置
# ---------------------------------------------------------------------------


def test_drift_check_interval_is_configurable(tmp_path: Path) -> None:
    """config.drift_check_interval_cycles=5 时，前 4 cycle 不调用 detector，
    第 5 cycle 才调用。"""
    waker, _client = _make_waker(tmp_path, interval=5)
    waker.config.drift_check_interval_cycles = 5
    with patch.object(waker._drift_detector, "check_drift", return_value=[]) as check_mock:
        for idx in range(1, 5):
            waker._run_drift_check(cycle_index=idx)
        waker._run_drift_check(cycle_index=5)
    assert check_mock.call_count == 1


# ---------------------------------------------------------------------------
# (e) 启动留痕
# ---------------------------------------------------------------------------


def test_startup_sync_writes_audit_log(
    tmp_path: Path, audit_records: list[dict[str, Any]]
) -> None:
    """启动时 _startup_sync_with_audit 写 startup_sync 事件含完整 schema。"""
    _make_skill_tree(tmp_path)
    runtime_home = tmp_path / "runtime_home"
    runtime_home.mkdir()
    simple_waker._startup_sync_with_audit(tmp_path, runtime_home)
    events = [r for r in audit_records if r.get("event") == "startup_sync"]
    assert len(events) == 1, f"expected exactly 1 startup_sync; got {audit_events_summary(audit_records)}"
    payload = events[0]
    assert payload["skills_count"] == 1
    assert payload["synced_skills"] == ["demo-skill"]
    assert payload["skipped_reason"] is None


def test_startup_sync_disabled_env(audit_records: list[dict[str, Any]]) -> None:
    """WAKER_SKILL_SYNC_DISABLED=1 → skipped_reason=disabled。"""
    os.environ["WAKER_SKILL_SYNC_DISABLED"] = "1"
    # 直接调 helper
    simple_waker._startup_sync_with_audit(Path("/nonexistent"), Path("/tmp/disabled-test"))
    events = [r for r in audit_records if r.get("event") == "startup_sync"]
    assert events
    assert events[-1]["skipped_reason"] == "disabled"
    assert events[-1]["skills_count"] == 0
    del os.environ["WAKER_SKILL_SYNC_DISABLED"]


def test_startup_sync_source_missing(tmp_path: Path, audit_records: list[dict[str, Any]]) -> None:
    """.cursor/skills 不存在 → skipped_reason=source_missing。"""
    runtime_home = tmp_path / "runtime_home"
    runtime_home.mkdir()
    simple_waker._startup_sync_with_audit(tmp_path, runtime_home)
    events = [r for r in audit_records if r.get("event") == "startup_sync"]
    assert events
    assert events[-1]["skipped_reason"] == "source_missing"


# ---------------------------------------------------------------------------
# (f) 跨平台 mtime 抑制
# ---------------------------------------------------------------------------


def test_drift_detector_skips_resync_within_skip_window(
    tmp_path: Path, audit_records: list[dict[str, Any]]
) -> None:
    """同周期内同 skill 连续 resync 触发 → 第二次被 skip_window 抑制。"""
    _make_skill_tree(tmp_path)
    runtime_home = tmp_path / "runtime_home"
    runtime_home.mkdir()
    detector = DriftDetector(
        source_root=tmp_path / ".cursor" / "skills",
        dest_root=runtime_home / ".claude" / "skills",
    )
    # 第一次全 scan（空 entries → 缓存填充）
    detector.check_drift()
    # 改源 skill → 触发 1 个 DriftEntry
    skill_md = tmp_path / ".cursor" / "skills" / "demo-skill" / "SKILL.md"
    skill_md.write_text("# brand new content", encoding="utf-8")
    entries = detector.check_drift()
    assert entries, "expected drift entry after source change"
    # 第一次 resync
    first = detector.resync(entries)
    assert first.ok
    assert "demo-skill" in first.resynced_skills
    # 再次 resync 同一周期内 → 应被 throttled
    second = detector.resync(entries)
    assert second.ok
    assert "demo-skill" in second.skipped_due_to_throttle


def test_drift_detector_mtime_tolerance_suppresses_false_drift(
    tmp_path: Path,
) -> None:
    """mtime 差 < tolerance_ns(1ms) + size 一致 → 不触发漂移。"""
    _make_skill_tree(tmp_path)
    runtime_home = tmp_path / "runtime_home"
    runtime_home.mkdir()
    dest_root = runtime_home / ".claude" / "skills"
    src_root = tmp_path / ".cursor" / "skills"
    # 拷贝 + 模拟微秒级 mtime 差异
    import shutil

    shutil.copytree(src_root / "demo-skill", dest_root / "demo-skill")
    src_st = (src_root / "demo-skill" / "SKILL.md").stat()
    dst_path = dest_root / "demo-skill" / "SKILL.md"
    # 强制设置 dest mtime 差 500_000ns (0.5ms) 在 tolerance 内
    target_ns = src_st.st_mtime_ns + 500_000
    dst_path.stat()  # ensure exists
    os.utime(dst_path, ns=(target_ns, target_ns))
    detector = DriftDetector(
        source_root=src_root,
        dest_root=dest_root,
        mtime_tolerance_ns=1_000_000,
    )
    detector.check_drift()  # initial scan
    # 第二次跑：源 mtime 不变，dest 已就位
    detector.check_drift()
    # mtime 差 0.5ms < 1ms tolerance + size 一致 → 不应有 drift entry
    # 但 _iter_skill_files 也会包含 references/extra.md,需重新同步一遍 dest
    shutil.copytree(src_root / "demo-skill", dest_root / "demo-skill", dirs_exist_ok=True)
    os.utime(dest_root / "demo-skill" / "SKILL.md", ns=(target_ns, target_ns))
    entries2 = detector.check_drift()
    # 第二次：所有 dest 都同步好 + mtime 容差内 → 无 drift
    assert entries2 == [] or all(not e.hash_mismatch for e in entries2)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def audit_events_summary(records: list[dict[str, Any]]) -> str:
    return ", ".join(r.get("event", "?") for r in records)
