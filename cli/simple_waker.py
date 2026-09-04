"""Thin MAP waker: poll unified work snapshot, remind agent when there is new work.

Waker logic stays minimal: the platform serves ``GET /agents/me/work``
(whoami + topic-progress + todos + wakeable notifications); agents use
``map work`` / ``map topic progress`` in Skills.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import signal
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import typer

from cli.agent_client import apply_project_claude_env
from cli.bridge_state import load_bridge_state
from cli.cursor_wake_backend import apply_project_cursor_env
from cli.errors import WorkerError
from cli.interactive_bridge import bridge_state_path
from cli.interactive_bridge import load_state as _load_bridge_state
from cli.map_command_client import MapCommandClient
from cli.map_sdk_client import MapSdkClient
from cli.wake_backend import (
    WakeBackend,
    build_wake_backend,
    resolve_waker_runtime,
    sync_runtime_skills,
)
from cli.waker_checks import SimpleWakerChecksMixin
from cli.waker_context import (  # noqa: F401  (_filter…: test import compat)
    SimpleWakerConfig,
    SimpleWakerStats,
    WakeContext,
    _filter_topic_progress_for_persona,
    _parse_datetime,
    build_remind_prompt,
    build_wake_context,
    build_waker_client,
    next_sleep_seconds,
    should_send_remind,
    summarize_pending_work,
    wake_signature,
)
from cli.waker_state import SimpleWakerStateMixin
from cli.worker_cycle_log import log_cycle_summary

APP = typer.Typer(add_completion=False)

_skill_audit_logger = logging.getLogger("cli.simple_waker.skill_audit")
_audit_drift_logger = logging.getLogger("cli.simple_waker.verify_audit")


def _startup_sync_with_audit(project_root: Path, runtime_home: Path) -> None:
    """启动时同步 skills 并按 plan v0.x §A5 固定 JSON schema 留痕。

    skipped_reason 枚举: ``source_missing`` (源 .cursor/skills 不存在)
    / ``permission_denied`` (PermissionError 派生) / ``disabled`` (显式
    关闭: 通过环境变量 ``WAKER_SKILL_SYNC_DISABLED=1`` 跳过)。
    """
    if os.environ.get("WAKER_SKILL_SYNC_DISABLED") == "1":
        _skill_audit_logger.info(
            json.dumps(
                {
                    "event": "startup_sync",
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "skills_count": 0,
                    "synced_skills": [],
                    "skipped_reason": "disabled",
                },
                ensure_ascii=False,
            )
        )
        return
    try:
        synced, skipped_reason = sync_runtime_skills(
            project_root=project_root, runtime_home=runtime_home
        )
    except PermissionError as exc:
        _skill_audit_logger.warning(
            json.dumps(
                {
                    "event": "startup_sync",
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "skills_count": 0,
                    "synced_skills": [],
                    "skipped_reason": "permission_denied",
                    "error": f"{type(exc).__name__}: {exc}",
                },
                ensure_ascii=False,
            )
        )
        return
    _skill_audit_logger.info(
        json.dumps(
            {
                "event": "startup_sync",
                "ts": datetime.now(timezone.utc).isoformat(),
                "skills_count": len(synced),
                "synced_skills": synced,
                "skipped_reason": skipped_reason,
            },
            ensure_ascii=False,
        )
    )

RUNTIME_CONTRACT_VERSION = "simple-waker-runtime-contract-v3"
RUNTIME_CONTRACT_FILES: tuple[str, ...] = (
    ".cursor/skills/map-project-collab/SKILL.md",
    ".cursor/skills/topic-host/SKILL.md",
    ".cursor/skills/topic-participant/SKILL.md",
    ".cursor/skills/experiment-host/SKILL.md",
    ".cursor/skills/experiment-reviewer/SKILL.md",
)


def runtime_contract_hash(project_root: Path) -> str:
    """Hash the runtime-facing prompt/Skill contract that may affect agent behavior."""
    digest = hashlib.sha256()
    digest.update(RUNTIME_CONTRACT_VERSION.encode("utf-8"))
    for relative in RUNTIME_CONTRACT_FILES:
        path = project_root / relative
        digest.update(relative.encode("utf-8"))
        if path.is_file():
            digest.update(path.read_bytes())
        else:
            digest.update(b"<missing>")
    return digest.hexdigest()



class SimpleWaker(SimpleWakerChecksMixin, SimpleWakerStateMixin):
    def __init__(
        self,
        *,
        client: MapCommandClient | MapSdkClient,
        config: SimpleWakerConfig | None = None,
        backend: WakeBackend | None = None,
    ) -> None:
        self.client = client
        self.config = config or SimpleWakerConfig()
        # 实验 d12c328c I1：从 env 解析 expected_remind_runtime_minutes（CLI flag
        # 暂未暴露，与 server 侧 Settings 同源；env override 即可）。
        if self.config.expected_remind_runtime_minutes is None:
            env_val = os.environ.get("MAP_EXPECTED_REMIND_RUNTIME_MINUTES")
            if env_val:
                try:
                    self.config.expected_remind_runtime_minutes = int(env_val)
                except ValueError:
                    warnings.warn(
                        f"MAP_EXPECTED_REMIND_RUNTIME_MINUTES={env_val!r} is not a "
                        "valid integer; falling back to 30min default",
                        RuntimeWarning,
                        stacklevel=2,
                    )
                    self.config.expected_remind_runtime_minutes = 30
            else:
                self.config.expected_remind_runtime_minutes = 30
        self.state = load_bridge_state(
            self.config.state_file,
            bridge_name="simple-waker",
            default_collections=("personas",),
        )
        # 实验 d12c328c I1：每个 persona state 镜像 expected_remind_runtime_seconds
        # （与 server 侧 Settings.expected_remind_runtime_minutes 同源）；CLI 视图层
        # 读此字段推导 busy 容忍（fallback chain：state.json > env > 30min default）。
        # setdefault 保证旧 state 不被覆盖（首次启动后写一次，后续保留）。
        persona_state = self._persona_state(self.config.persona)
        persona_state.setdefault(
            "expected_remind_runtime_seconds",
            int(self.config.expected_remind_runtime_minutes or 30) * 60,
        )
        self._state_dirty = True
        self._save_state_if_needed(force=True)
        self._state_dirty = False
        self._inflight = False
        # T03：本周期解析出的 persona 身份（work 快照的 agent 字段）。
        # 缓存后 action_item escalation 等下游消费点不再重复调 whoami 子进程。
        self._me: dict[str, Any] | None = None
        # T39：连续失败计数（指数退避）与优雅退出标志（SIGTERM/SIGINT）。
        self._consecutive_errors = 0
        self._stop_requested = False
        self._stop_event: asyncio.Event | None = None
        self.backend: WakeBackend = backend or build_wake_backend(
            runtime=self.config.runtime,
            project_root=self.config.project_root,
            persona=self.config.persona,
            get_agent_state=lambda: self._persona_state(self.config.persona),
            save_state_fn=lambda: self._save_state_if_needed(force=True),
            model=self.config.model,
            runtime_home=self.config.runtime_home,
        )
        self._runtime_contract_hash = runtime_contract_hash(self.config.project_root)
        # 实验 waker-runtime-skill-hotcheck I4：构造 DriftDetector。
        # source_root = .cursor/skills；dest_root = <runtime_home>/.claude/skills。
        # 若 source_root 不存在（极端场景），detector 退化为空 scan，resync 由
        # detector.resync 走 source_missing 路径——不抛异常。
        self._drift_detector = self._build_drift_detector()
        # 实验 b3ec2e4d I2：启动时回收上次 crash 残留的 busy 标记。
        # state 文件若含 stale busy_started_at + 已死 PID → 清零本地 + server。
        self._check_busy_crash_recovery()

    def run_forever(self) -> SimpleWakerStats:
        return asyncio.run(self._run_forever_async())

    async def _run_forever_async(self) -> SimpleWakerStats:
        if not self.config.dry_run:
            await self._reset_runtime_session_if_contract_changed()
            await self._reset_runtime_session_if_backend_changed()
        if not self.config.dry_run:
            await self.backend.connect()
        # T39：优雅退出——SIGTERM/SIGINT 置 stop 标志并唤醒 sleep，当前
        # cycle 结束后走 finally 的 disconnect（原先 KeyboardInterrupt 会
        # 直接炸出 asyncio.run，backend.disconnect() 不保证执行）。
        stop_event = asyncio.Event()
        self._stop_event = stop_event
        loop = asyncio.get_running_loop()
        registered_signals: list[int] = []
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, self._request_stop)
                registered_signals.append(sig)
            except (NotImplementedError, RuntimeError):
                # Windows / 非主线程：无 add_signal_handler。SIGINT 仍有
                # KeyboardInterrupt 默认路径，asyncio.run 的 finally 兜底。
                pass
        total = SimpleWakerStats()
        try:
            while True:
                try:
                    stats, sleep_for = await self._run_once_async()
                    self._consecutive_errors = 0
                except WorkerError as exc:
                    # 瞬时错误兜底：API 5xx / 子进程失败 / 身份解析失败等。
                    # 长驻 waker 不能因单次 cycle 失败退出——记错到 state，
                    # 按指数退避（T39）后下一 cycle 重试。持续失败会在
                    # state 累积 last_cycle_error 供运维观测。
                    typer.echo(f"[simple-waker:cycle-error] {exc}", err=True)
                    persona_state = self._persona_state(self.config.persona)
                    persona_state["last_cycle_error"] = str(exc)
                    persona_state["last_cycle_error_at"] = datetime.now(timezone.utc).isoformat()
                    self._state_dirty = True
                    self._save_state_if_needed(force=True)
                    self._consecutive_errors += 1
                    stats = SimpleWakerStats(cycles=1, cycle_errors=1)
                    sleep_for = self._backoff_interval()
                total.add(stats)
                # 实验 waker-runtime-skill-hotcheck I4：每 N 个 poll cycle 跑
                # 一次漂移检测；用 total.cycles 计数保证无论正常/异常路径都
                # 计数。失败已在 _run_drift_check 内捕获，不阻塞主流程。
                self._run_drift_check(cycle_index=total.cycles)
                # 实验 e6d23886 (T7-a) I3：verify-audit 检测同样按 N cycle 节流，
                # 失败/异常在 _run_verify_audit_check 内部捕获，不阻断 waker。
                self._run_verify_audit_check(cycle_index=total.cycles)
                log_cycle_summary(
                    "simple-waker",
                    total,
                    fields=[
                        "cycles",
                        "polls_with_work",
                        "polls_idle",
                        "reminds_sent",
                        "remind_skips_busy",
                        "remind_skips_cooldown",
                        "remind_skips_unchanged",
                        "remind_skips_bridge_active",
                        "remind_errors",
                        "dry_run_actions",
                        "inbound_events_recorded",
                        "inbound_events_duplicate",
                        "inbound_events_skipped",
                        "action_items_wake",
                        "action_items_stale",
                        "action_items_skip",
                        "action_items_errors",
                        "cycle_errors",
                        "session_resets_topic_switch",
                    ],
                )
                if self.config.once:
                    break
                if self.config.max_cycles is not None and total.cycles >= self.config.max_cycles:
                    break
                if self._stop_requested:
                    # T39：信号已到——当前 cycle 已完整收尾，直接退出走
                    # finally 的 disconnect，不再进入下一个退避/轮询间隔。
                    typer.echo("[simple-waker] stop requested; exiting gracefully", err=True)
                    break
                # T39：sleep 可被 stop 信号提前唤醒（wait_for + Event），
                # 避免收到 SIGTERM 后还要空等最长 30min 的退避间隔。
                # asyncio.wait_for 超时在 Py3.10 抛 ``asyncio.TimeoutError``
                # （3.11 才与内置 TimeoutError 合并），故用 asyncio 版本捕获。
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(stop_event.wait(), timeout=sleep_for)
            return total
        finally:
            for sig in registered_signals:
                with contextlib.suppress(Exception):
                    loop.remove_signal_handler(sig)
            self._stop_event = None
            if not self.config.dry_run:
                await self.backend.disconnect()
            close_fn = getattr(self.client, "close", None)
            if callable(close_fn):
                with contextlib.suppress(Exception):
                    close_fn()

    def _request_stop(self) -> None:
        """Signal handler body: set the stop flag and wake the sleep."""
        if self._stop_requested:
            return
        self._stop_requested = True
        if self._stop_event is not None:
            self._stop_event.set()

    def _backoff_interval(self) -> float:
        """T39：按连续失败次数指数退避（idle × 2^n，cap 30min）。"""
        backoff_cap = 1800.0
        factor = 2 ** min(self._consecutive_errors, 8)
        return min(self.config.idle_interval * factor, backoff_cap)

    def run_once(self) -> SimpleWakerStats:
        return asyncio.run(self._run_once_async())[0]

    async def _run_once_async(self) -> tuple[SimpleWakerStats, float]:
        stats = SimpleWakerStats(cycles=1)
        self._scan_stalled_experiment_locks(stats)
        # 实验 waker-status-view I2: 每个 cycle 自写 waker 状态指标 + pid 变化归档
        # 必须放在 _scan_stalled_experiment_locks 之后、所有 return 之前，确保
        # 每个 cycle（无论 remind / dry-run / no-remind 路径）都累加计数。
        self._accumulate_cycle_stats(stats)
        work = self.client.work() or {}
        # T03：work 快照本身含完整 agent 身份（AgentWorkRead.agent），且认证
        # 失败时 work 子进程同样非零退出——身份直接从快照取，省掉每周期一次
        # 独立的 whoami 子进程（完整 Python + Typer 冷启动）。
        self._ensure_identity(work)
        topic_progress_data = work.get("topic_progress") or {}
        todos = work.get("todos") or {}
        notifications_payload = work.get("notifications") or {}
        notifications = (
            list(notifications_payload.get("items") or []) if isinstance(notifications_payload, dict) else []
        )
        open_topics: list[dict[str, Any]] = []
        if self.config.drain_topics and self.config.persona == "host":
            open_topics = self.client.topic_list_open()
        context = build_wake_context(
            topic_progress_data=topic_progress_data,
            todos=todos,
            notifications=notifications,
            persona=self.config.persona,
            drain_topics=self.config.drain_topics,
            open_topics=open_topics,
            max_prompt_topics=self.config.max_prompt_topics,
        )
        if context.has_work:
            stats.polls_with_work = 1
        else:
            stats.polls_idle = 1

        persona_state = self._persona_state(self.config.persona)
        last_remind_at = _parse_datetime(persona_state.get("last_remind_at"))
        now = datetime.now(timezone.utc)
        signature = wake_signature(context)
        should_remind, skip_reason = should_send_remind(
            context,
            now=now,
            last_remind_at=last_remind_at,
            inflight=self._inflight,
            min_remind_seconds=self.config.min_remind_seconds,
            signature=signature,
            last_reminded_signature=persona_state.get("last_wake_signature"),
            max_silence_seconds=self.config.max_silence_seconds,
        )
        if not should_remind:
            if skip_reason == "busy":
                stats.remind_skips_busy = 1
            elif skip_reason == "cooldown":
                stats.remind_skips_cooldown = 1
            elif skip_reason == "unchanged":
                stats.remind_skips_unchanged = 1
            self._save_state_if_needed()
            return stats, next_sleep_seconds(context, self.config)

        # 实验 db97aeac I3（A3）：交互桥接软信号——桥接 state 的
        # last_seen_at 在活跃窗口内说明交互会话在场，waker 降级跳过本次
        # 唤醒。宁重复不遗漏：state 缺失/过期/损坏时不降级，行为与现状一致。
        if self._interactive_bridge_active(now):
            stats.remind_skips_bridge_active = 1
            self._save_state_if_needed()
            return stats, next_sleep_seconds(context, self.config)

        prompt = build_remind_prompt(self.config.persona, context)
        if self.config.dry_run:
            typer.echo(
                f"[dry-run] would remind persona={self.config.persona} "
                f"topics={context.topic_update_count} todos={context.todo_item_count}"
            )
            typer.echo(prompt.rstrip())
            stats.dry_run_actions = 1
            self._save_state_if_needed()
            return stats, next_sleep_seconds(context, self.config)

        # v0.10：action_item escalation（移植自 legacy runtime-waker）。
        # 在 remind 前扫描 open + owner=persona 的 action_items，推进
        # wake_count（WAKE）或标记过期（STALE）。失败不阻塞 remind。
        self._apply_action_item_escalation(stats, todos=todos, now=now)

        # 话题边界会话重置（2026-08-31 成本战役结论）：唤醒工作集的话题
        # id 集合与上次唤醒不同（新话题出现/旧话题关闭）且将 resume 旧
        # 会话时，先 reset_session——旧话题完整历史不背进新会话。
        current_topic_ids = tuple(sorted({e.topic_id for e in context.topic_progress}))
        await self._reset_session_if_topic_switched(persona_state, current_topic_ids, stats)

        self._inflight = True
        # 实验 b3ec2e4d I2：进入 runtime 调用（remind → claude 子进程）
        # 前 touch busy 心跳，会话结束清零。失败兜底不阻塞主流程（A8）。
        self._touch_busy(stats, now=now)
        try:
            await self.backend.wake_async(prompt=prompt, event_source="simple-waker")
            persona_state["last_remind_at"] = now.isoformat()
            persona_state["last_remind_work_count"] = context.total_items
            persona_state["last_remind_topic_count"] = context.topic_update_count
            persona_state["last_wake_signature"] = signature
            persona_state["last_wake_topic_ids"] = list(current_topic_ids)
            self._state_dirty = True
            stats.reminds_sent = 1
            # v0.10：写一条聚合 inbound_event 作为可观测性审计。
            # fingerprint 含 cycle 时间戳，确保每个 remind cycle 唯一；
            # 同一 cycle 内 min_remind_seconds 已防重，409 仅作并发兜底。
            self._record_remind_inbound_event(stats, now=now, work_count=context.total_items)
        except WorkerError as exc:
            stats.remind_errors = 1
            persona_state["last_remind_error"] = str(exc)
            persona_state["last_remind_error_at"] = now.isoformat()
            self._state_dirty = True
            typer.echo(f"[simple-waker:error] {exc}", err=True)
        finally:
            # I2：busy 心跳清零（与 _touch_busy 配对），server 列
            # agents.last_busy_since → NULL；PID 校验防止 crash 漏清。
            self._clear_busy(stats)
            self._inflight = False
            self._save_state_if_needed(force=True)
        return stats, next_sleep_seconds(context, self.config)

    def _scan_stalled_experiment_locks(self, stats: SimpleWakerStats) -> None:
        """Ask the platform to materialize stalled-lock notifications before polling work."""
        if self.config.persona != "host":
            return
        scan_fn = getattr(self.client, "experiment_scan_stalled_locks", None)
        if scan_fn is None or self.config.dry_run:
            return
        try:
            result = scan_fn() or {}
        except WorkerError as exc:
            stats.cycle_errors += 1
            typer.echo(f"[simple-waker:stalled-lock-scan] {exc}", err=True)
            return
        if isinstance(result, dict):
            stats.stalled_lock_notifications += int(result.get("emitted_count") or 0)

    def _ensure_identity(self, work: dict[str, Any] | None = None) -> None:
        """Resolve the persona agent identity, preferring the work snapshot.

        快路径取 ``work["agent"]``；仅当快照缺身份（老版本 server / 测试
        mock）时回退独立 whoami 子进程。结果缓存在 ``self._me`` 供本周期
        下游消费（action_item escalation 的 owner 过滤）。
        """
        me = (work or {}).get("agent")
        if not me or not me.get("id"):
            me = self.client.whoami()
        if not me or not me.get("id"):
            raise WorkerError(
                f"Could not resolve {self.config.persona} identity; run "
                f"`map --persona {self.config.persona} persona whoami` first"
            )
        self._me = me

    def _persona_state(self, persona: str) -> dict[str, Any]:
        personas = self.state.setdefault("personas", {})
        if persona not in personas or not isinstance(personas[persona], dict):
            personas[persona] = {}
        return personas[persona]

    def _interactive_bridge_active(self, now: datetime) -> bool:
        """交互桥接活跃判定（单向软信号，db97aeac I3）。

        读 ``.map/interactive-bridge-state-<persona>.json`` 的
        ``last_seen_at``：在 ``bridge_active_seconds`` 窗口内 → True（waker
        降级跳过唤醒）。state 缺失 / 过期 / 损坏 / 窗口关闭（<=0）一律
        False——桥接侧任何异常都不改变 waker 现状行为。
        """
        window = self.config.bridge_active_seconds
        if window <= 0:
            return False
        try:
            state = _load_bridge_state(
                bridge_state_path(self.config.project_root, self.config.persona)
            )
        except Exception:  # noqa: BLE001 — 软信号不得影响 waker 主流程
            return False
        last_seen_at = _parse_datetime(state.get("last_seen_at"))
        if last_seen_at is None:
            return False
        return (now - last_seen_at).total_seconds() < window

    async def _reset_runtime_session_if_contract_changed(self) -> None:
        persona_state = self._persona_state(self.config.persona)
        previous_hash = persona_state.get("runtime_contract_hash")
        if previous_hash == self._runtime_contract_hash:
            return
        has_resume_session = bool(
            persona_state.get("claude_session_id")
            or persona_state.get("runtime_session_id")
            or persona_state.get("cursor_agent_id")
        )
        if previous_hash is not None or has_resume_session:
            typer.echo(
                f"[simple-waker] runtime contract changed for {self.config.persona}; starting a fresh runtime session",
                err=True,
            )
            await self.backend.reset_session()
        persona_state["runtime_contract_hash"] = self._runtime_contract_hash
        persona_state["runtime_contract_version"] = RUNTIME_CONTRACT_VERSION
        persona_state["runtime_contract_updated_at"] = datetime.now(timezone.utc).isoformat()
        self._state_dirty = True
        self._save_state_if_needed(force=True)

    async def _reset_runtime_session_if_backend_changed(self) -> None:
        persona_state = self._persona_state(self.config.persona)
        previous = persona_state.get("runtime_backend")
        if not previous or previous == self.config.runtime:
            return
        typer.echo(
            f"[simple-waker] runtime backend changed {previous} -> {self.config.runtime}; "
            "starting a fresh runtime session",
            err=True,
        )
        await self.backend.reset_session()

    async def _reset_session_if_topic_switched(
        self,
        persona_state: dict[str, Any],
        current_topic_ids: tuple[str, ...],
        stats: SimpleWakerStats,
    ) -> None:
        """话题切换/关闭 → 重置 runtime session（2026-08-31 成本战役结论）。

        会话复用（resume ``claude_session_id``）以话题为边界：同一话题的
        多轮讨论保留上下文连续；唤醒工作集的话题 id 集合与上次唤醒不同
        （新话题出现 / 旧话题关闭出队）且本次将 resume 旧会话时，先
        ``reset_session()`` 再唤醒。动机：cache 未生效的端点上旧话题
        完整历史每轮全价重发（实测单会话 16h / 23MB / 混 7 个话题）。

        仅在「有旧会话可复用」时才判断；无 ``claude_session_id`` 时唤醒
        本身就是新会话，直接返回。比较只认话题 id 集合——同集合内的
        新评论/轮次推进（签名已变、正常唤醒）不触发重置。
        """
        if not persona_state.get("claude_session_id"):
            return
        last_ids = tuple(persona_state.get("last_wake_topic_ids") or ())
        if tuple(current_topic_ids) == last_ids:
            return
        typer.echo(
            f"[simple-waker] topic set switched for {self.config.persona} "
            f"({list(last_ids) or '[]'} -> {list(current_topic_ids) or '[]'}); "
            "resetting runtime session",
            err=True,
        )
        await self.backend.reset_session()
        stats.session_resets_topic_switch = 1


@APP.command()
def run(
    persona: str = typer.Option("host", "--persona", help="MAP persona to wake."),
    project_root: Path = typer.Option(Path("."), "--project-root", help="Project root containing .map/."),
    map_cmd: str = typer.Option("map", "--map-cmd", help="MAP CLI command."),
    active_interval: float = typer.Option(
        30.0,
        "--active-interval",
        min=5.0,
        help="Poll/remind cadence while work exists.",
    ),
    idle_interval: float = typer.Option(
        300.0,
        "--idle-interval",
        min=30.0,
        help="Poll cadence when idle.",
    ),
    min_remind_seconds: float = typer.Option(
        30.0,
        "--min-remind-seconds",
        min=5.0,
        help="Minimum seconds between remind prompts while work remains.",
    ),
    max_silence_seconds: float = typer.Option(
        1800.0,
        "--max-silence-seconds",
        min=60.0,
        help="工作集签名不变时的强制唤醒兜底间隔（秒）；签名去重后超过该时长未唤醒则兜底提醒一次。",
    ),
    bridge_active_seconds: float = typer.Option(
        600.0,
        "--bridge-active-seconds",
        min=-1.0,
        help="交互桥接软信号窗口（秒）：桥接 state last_seen_at 在窗口内则降级跳过唤醒；<=0 关闭。",
    ),
    once: bool = typer.Option(False, "--once", help="Run one cycle and exit."),
    max_cycles: int | None = typer.Option(None, "--max-cycles", min=1, help="Stop after N cycles."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print remind actions without invoking runtime."),
    drain_topics: bool = typer.Option(
        False,
        "--drain-topics",
        help="Host mode: keep reminding while any open topic exists; dismiss does not count as done.",
    ),
    state_file: Path | None = typer.Option(
        Path(".map/simple-waker-state.json"),
        "--state-file",
        help="Runtime session + remind timestamps.",
    ),
    model: str | None = typer.Option(None, "--model", help="Optional agent model override."),
    runtime: str | None = typer.Option(
        None,
        "--runtime",
        help="Agent runtime: claude (default) or cursor. Env: MAP_SIMPLE_RUNTIME.",
    ),
    runtime_home: Path | None = typer.Option(
        None,
        "--runtime-home",
        help="Optional HOME for the Claude runtime process (ignored for --runtime cursor).",
    ),
    stale_threshold_minutes: int | None = typer.Option(
        None,
        "--waker-stale-threshold",
        min=0,
        help=(
            "Stale-open-topic threshold (minutes). When set, exports "
            "MAP_STALE_OPEN_TOPIC_THRESHOLD_MINUTES so subprocess "
            "``map work`` invocations and any in-process server pick up "
            "the override via server.config.Settings (f873c287 I1(g))."
        ),
    ),
    max_prompt_topics: int = typer.Option(
        3,
        "--max-prompt-topics",
        min=0,
        help=(
            "Top-K quota: max topic obligations listed per remind prompt "
            "(obligation first, oldest first). Excess items are deferred to "
            "the next remind automatically. 0 = unlimited (legacy behavior). "
            "Env fallback: MAP_SIMPLE_MAX_PROMPT_TOPICS."
        ),
    ),
    subprocess_client: bool = typer.Option(
        False,
        "--subprocess-client",
        help=(
            "T24 rollback: talk to MAP via `map` subprocess instead of "
            "in-process SDK. Env: MAP_WAKER_SUBPROCESS=1."
        ),
    ),
    drift_check_interval_cycles: int = typer.Option(
        30,
        "--drift-check-interval-cycles",
        min=0,
        help=(
            "实验 waker-runtime-skill-hotcheck I4：每 N 个 poll cycle 跑一次"
            " runtime skill 漂移检测；0 = 关闭。"
            " Env fallback: WAKER_DRIFT_CHECK_INTERVAL_CYCLES。"
        ),
    ),
) -> None:
    """Run the simplified MAP waker loop."""
    root = project_root.resolve()
    try:
        resolved_runtime = resolve_waker_runtime(runtime)
    except WorkerError as exc:
        typer.echo(f"[simple-waker] {exc}", err=True)
        raise typer.Exit(code=2) from exc
    # LLM 凭据权威来源按 runtime 分流：Claude 走 .map/.claude-env，Cursor 走
    # .map/.cursor-env。直启（绕过 start-*.sh）也强制以文件为准，避免继承
    # shell 残留端点/账号。
    if resolved_runtime == "cursor":
        apply_project_cursor_env(root)
    else:
        apply_project_claude_env(root)
    resolved_runtime_home = runtime_home
    if resolved_runtime != "cursor" and resolved_runtime_home is not None and not dry_run:
        _startup_sync_with_audit(root, resolved_runtime_home)
    # f873c287 I1(g): apply threshold to env BEFORE any Settings read so
    # the ``map work`` subprocess (and any in-process server) sees the
    # override on its first ``get_settings()`` call. We export here even
    # in dry-run so logs reflect what the value would be in production.
    if stale_threshold_minutes is not None:
        os.environ["MAP_STALE_OPEN_TOPIC_THRESHOLD_MINUTES"] = str(stale_threshold_minutes)
        # If the server module is already imported in this process (test
        # fixtures), invalidate the lru_cache so the new value wins.
        try:
            from server.config import get_settings

            get_settings.cache_clear()
        except Exception:
            pass
    client = build_waker_client(
        persona=persona,
        project_root=root,
        map_cmd=map_cmd,
        dry_run=False,
        subprocess_client=subprocess_client,
    )
    # env fallback：不带 flag 启动（如 scripts/start-simple-waker.sh 直传旧参数）
    # 时仍可经 MAP_SIMPLE_MAX_PROMPT_TOPICS 调配额。
    if max_prompt_topics == 3:
        env_value = os.environ.get("MAP_SIMPLE_MAX_PROMPT_TOPICS")
        if env_value:
            try:
                max_prompt_topics = max(0, int(env_value))
            except ValueError:
                typer.echo(
                    f"[simple-waker] ignore invalid MAP_SIMPLE_MAX_PROMPT_TOPICS={env_value!r}",
                    err=True,
                )
    # I4: drift check 周期默认 30；env WAKER_DRIFT_CHECK_INTERVAL_CYCLES 覆盖。
    if drift_check_interval_cycles == 30:
        env_value = os.environ.get("WAKER_DRIFT_CHECK_INTERVAL_CYCLES")
        if env_value:
            try:
                drift_check_interval_cycles = max(0, int(env_value))
            except ValueError:
                typer.echo(
                    f"[simple-waker] ignore invalid WAKER_DRIFT_CHECK_INTERVAL_CYCLES={env_value!r}",
                    err=True,
                )
    config = SimpleWakerConfig(
        persona=persona,
        project_root=root,
        map_cmd=map_cmd,
        active_interval=active_interval,
        idle_interval=idle_interval,
        once=once,
        max_cycles=max_cycles,
        dry_run=dry_run,
        state_file=state_file,
        model=model,
        runtime=resolved_runtime,
        runtime_home=resolved_runtime_home,
        min_remind_seconds=min_remind_seconds,
        max_silence_seconds=max_silence_seconds,
        bridge_active_seconds=bridge_active_seconds,
        drain_topics=drain_topics,
        max_prompt_topics=max_prompt_topics,
        stale_threshold_minutes=stale_threshold_minutes,
        drift_check_interval_cycles=drift_check_interval_cycles,
    )
    waker = SimpleWaker(client=client, config=config)
    waker.run_forever()


if __name__ == "__main__":
    APP()
