"""SimpleWaker 的周期性检查 mixin — T45 拆分自 cli/simple_waker.py。

drift 检测（``_run_drift_check``）、verify-audit 扫描
（``_run_verify_audit_check``）、inbound_event 审计与 action_item 升级
（``_apply_action_item_escalation``）。审计 logger 名固定为
``cli.simple_waker.skill_audit`` / ``cli.simple_waker.verify_audit``——
测试 listener 按该名字挂载，模块迁移不改名。
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import typer

from cli.action_item_escalation import ActionItemWakeDecision, scan_pending_action_items
from cli.drift_detector import DriftDetector
from cli.errors import WorkerError
from cli.waker_context import SimpleWakerStats

# logger 名固定（测试按名字挂 listener；与宿主 _startup_sync 用同一对象）
_skill_audit_logger = logging.getLogger("cli.simple_waker.skill_audit")
_audit_drift_logger = logging.getLogger("cli.simple_waker.verify_audit")


class SimpleWakerChecksMixin:
    """周期性检查与升级：drift / verify-audit / inbound_event / action_item。"""

    def _build_drift_detector(self) -> DriftDetector | None:
        """构造 DriftDetector；runtime_home 缺失或源 skills 不存在时返回 None。

        source_root = ``<project_root>/.cursor/skills``；
        dest_root   = ``<runtime_home>/.claude/skills``。
        """
        runtime_home = self.config.runtime_home
        if runtime_home is None:
            return None
        source_root = self.config.project_root / ".cursor" / "skills"
        if not source_root.is_dir():
            return None
        return DriftDetector(
            source_root=source_root,
            dest_root=runtime_home / ".claude" / "skills",
        )

    def _run_drift_check(self, *, cycle_index: int) -> None:
        """每 ``drift_check_interval_cycles`` 周期跑一次漂移检测 + 立即重同步。

        - 漂移为空 → 写 ``drift_no_change`` 审计行（无 alert）
        - 漂移非空且 resync 成功 → ``drift_resync`` 审计行（alert=True 仅在失败时）
        - resync 失败 → ``drift_resync_failed`` 审计行（alert=True），但不让 waker 崩
        """
        interval = self.config.drift_check_interval_cycles
        if interval <= 0 or self._drift_detector is None:
            return
        if cycle_index % interval != 0:
            return
        try:
            entries = self._drift_detector.check_drift()
        except Exception as exc:
            _skill_audit_logger.warning(
                json.dumps(
                    {
                        "event": "drift_check_failed",
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "cycle": cycle_index,
                        "alert": True,
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                    ensure_ascii=False,
                )
            )
            return
        if not entries:
            _skill_audit_logger.debug(
                json.dumps(
                    {
                        "event": "drift_no_change",
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "cycle": cycle_index,
                    },
                    ensure_ascii=False,
                )
            )
            return
        result = self._drift_detector.resync(entries)
        payload = {
            "event": "drift_resync" if result.ok else "drift_resync_failed",
            "ts": datetime.now(timezone.utc).isoformat(),
            "cycle": cycle_index,
            "drift_skills": sorted({e.skill_relpath.split("/", 1)[0] for e in entries}),
            "resynced_skills": result.resynced_skills,
            "duration_ms": result.duration_ms,
            "throttled": result.skipped_due_to_throttle,
            "alert": not result.ok,
        }
        if not result.ok:
            payload["error"] = result.error
        log_fn = _skill_audit_logger.warning if not result.ok else _skill_audit_logger.info
        log_fn(json.dumps(payload, ensure_ascii=False))

    def _run_verify_audit_check(self, *, cycle_index: int) -> None:
        """每 ``drift_check_interval_cycles`` 周期跑一次 verify-audit 扫描。

        实验 e6d23886 (T7-a) I3：复用 T4 drift hotcheck 的 30-cycle 节流节奏，
        但只扫描 + WARN 上报，不写 audit.jsonl (防递归绕过)。失败/异常被内部
        捕获，不阻断 waker 主流程。

        - 干净 → DEBUG ``audit_drift_clean``
        - 有漂移 → WARNING ``audit_drift_detected`` (alert=True)
        - 扫描异常 → WARNING ``audit_drift_check_failed`` (alert=True)
        """
        interval = self.config.drift_check_interval_cycles
        if interval <= 0:
            return
        if cycle_index % interval != 0:
            return
        try:
            from cli.verify_audit import scan_plane_audit
            detector = scan_plane_audit(self.config.project_root)
        except Exception as exc:
            _audit_drift_logger.warning(
                json.dumps(
                    {
                        "event": "audit_drift_check_failed",
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "cycle": cycle_index,
                        "alert": True,
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                    ensure_ascii=False,
                )
            )
            return
        if detector.count == 0:
            _audit_drift_logger.debug(
                json.dumps(
                    {
                        "event": "audit_drift_clean",
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "cycle": cycle_index,
                    },
                    ensure_ascii=False,
                )
            )
            return
        _audit_drift_logger.warning(
            json.dumps(
                {
                    "event": "audit_drift_detected",
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "cycle": cycle_index,
                    "drift_count": detector.count,
                    "drift_ids": [d.drift_id for d in detector.drifts],
                    "kinds": sorted({d.kind for d in detector.drifts}),
                    "alert": True,
                },
                ensure_ascii=False,
            )
        )

    def _record_remind_inbound_event(
        self,
        stats: SimpleWakerStats,
        *,
        now: datetime,
        work_count: int,
    ) -> None:
        """写一条聚合 inbound_event 审计行（fingerprint=simple-remind:{persona}:{ts}）。

        服务端 ``POST /agents/me/inbound-events`` 的 ``UNIQUE(fingerprint)``
        约束保证同一 cycle 不会被并发进程重复记录。失败不阻塞主流程。
        """
        record_fn = getattr(self.client, "inbound_event_record", None)
        if record_fn is None:
            # 测试 mock 可能不实现此方法；统计但不上报。
            stats.inbound_events_skipped = 1
            return
        fingerprint = f"simple-remind:{self.config.persona}:{now.strftime('%Y%m%dT%H%M%S%fZ')}"
        event_id = str(uuid.uuid4())
        try:
            recorded = record_fn(
                event_id=event_id,
                fingerprint=fingerprint,
                event_type="simple-waker.remind",
                source="polling",
            )
            if recorded:
                stats.inbound_events_recorded = 1
            else:
                stats.inbound_events_duplicate = 1
        except WorkerError as exc:
            # 审计写入失败不影响主流程；记录到 state 供运维排查。
            typer.echo(f"[simple-waker:inbound_event] {exc}", err=True)
            stats.inbound_events_skipped = 1
            persona_state = self._persona_state(self.config.persona)
            persona_state["last_inbound_event_error"] = str(exc)
            persona_state["last_inbound_event_error_at"] = now.isoformat()
            self._state_dirty = True

    def _apply_action_item_escalation(
        self,
        stats: SimpleWakerStats,
        *,
        todos: dict[str, Any],
        now: datetime,
    ) -> None:
        """推进 action_item 升级时间线（WAKE → mark-wake-sent，STALE → mark-stale）。

        simple-waker 是批量 remind，不像 legacy runtime-waker 逐项 fingerprint
        wake。但 action_item 的 ``wake_count`` 仍需在 remind 时推进，否则
        永远停在第一阶段。STALE 项标记后从下次 remind 候选里消失（服务端
        ``action_items`` todo 只返回 ``stale_at IS NULL`` 的项）。
        """
        items = todos.get("action_items") if isinstance(todos, dict) else None
        if not isinstance(items, list) or not items:
            return
        # 取 persona agent_id 用于 owner 过滤。身份已在 _run_once_async 的
        # _ensure_identity 从 work 快照解析并缓存（T03），这里直接复用，
        # 不再每周期多起一次 whoami 子进程。测试 mock 可能未初始化，兜底空。
        me = self._me or {}
        persona_agent_id = str(me.get("id") or "") or None
        decisions = scan_pending_action_items(
            items,
            persona_agent_id=persona_agent_id,
            now=now,
        )
        if not decisions:
            return
        mark_wake_fn = getattr(self.client, "action_mark_wake_sent", None)
        mark_stale_fn = getattr(self.client, "action_mark_stale", None)
        for item_id, decision in decisions:
            if decision == ActionItemWakeDecision.SKIP:
                stats.action_items_skip += 1
                continue
            if decision == ActionItemWakeDecision.STALE:
                if self.config.dry_run or mark_stale_fn is None:
                    stats.action_items_stale += 1
                    continue
                try:
                    mark_stale_fn(item_id)
                    stats.action_items_stale += 1
                except WorkerError as exc:
                    typer.echo(f"[simple-waker:mark-stale] {exc}", err=True)
                    stats.action_items_errors += 1
                continue
            # WAKE
            if self.config.dry_run or mark_wake_fn is None:
                stats.action_items_wake += 1
                continue
            try:
                mark_wake_fn(item_id)
                stats.action_items_wake += 1
            except WorkerError as exc:
                typer.echo(f"[simple-waker:mark-wake-sent] {exc}", err=True)
                stats.action_items_errors += 1
