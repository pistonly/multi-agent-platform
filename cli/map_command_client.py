from __future__ import annotations

import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

import typer
import yaml

from cli.host_worker_types import WorkerError


class MapCommandClient:
    def __init__(
        self,
        *,
        map_cmd: str = "map",
        persona: str = "host",
        project_root: Path | None = None,
        dry_run: bool = False,
        cmd_timeout: float = 120.0,
    ) -> None:
        self.map_cmd = map_cmd
        self.persona = persona
        self.project_root = project_root
        self.dry_run = dry_run
        # 每次 ``map`` 子进程调用的最大秒数。waker 每个 cycle 会发起多次
        # subprocess（whoami / work / action mark-* / inbound-event record …）；
        # 一条挂起的命令若没有超时保护，会把整个 waker cycle 永久阻塞
        # （_inflight 永远回不到 False，后续 cycle 全部 skip("busy")）。
        self.cmd_timeout = cmd_timeout

    def _base_args(self) -> list[str]:
        args = [self.map_cmd, "--persona", self.persona]
        if self.project_root is not None:
            args.extend(["--project-root", str(self.project_root)])
        return args

    def _run(self, args: list[str], *, parse_yaml: bool = True, retryable: bool = False) -> Any:
        cmd = self._base_args() + args
        if self.dry_run and _is_write_command(args):
            typer.echo("[dry-run] " + " ".join(cmd))
            return None
        attempts = _RETRY_ATTEMPTS if retryable else 1
        result: subprocess.CompletedProcess[str] | None = None
        for attempt in range(attempts):
            try:
                result = subprocess.run(
                    cmd,
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=self.cmd_timeout,
                )
            except subprocess.TimeoutExpired as exc:
                if retryable and attempt < attempts - 1:
                    self._retry_backoff(attempt, cmd, reason="timeout")
                    continue
                raise WorkerError(
                    f"Command timed out after {self.cmd_timeout}s: {' '.join(cmd)}"
                ) from exc
            if result.returncode == 0:
                break
            detail = result.stderr.strip() or result.stdout.strip()
            # 仅对幂等读命令、且失败特征为瞬时（API 5xx / 网络抖动 / 超时）时退避重试；
            # 401/403/404 等确定性错误或写命令失败立即抛出，避免无谓重试。
            if retryable and attempt < attempts - 1 and _is_transient_failure(detail):
                self._retry_backoff(attempt, cmd, reason=detail)
                continue
            raise WorkerError(
                f"Command failed ({result.returncode}): {' '.join(cmd)}\n{detail}"
            )
        assert result is not None  # 循环只在 returncode == 0 时 break
        if not parse_yaml:
            return result.stdout
        if not result.stdout.strip():
            return None
        return yaml.safe_load(result.stdout)

    def _retry_backoff(self, attempt: int, cmd: list[str], *, reason: str) -> None:
        delay = _RETRY_BACKOFF_BASE * (2**attempt)
        typer.echo(
            f"[map-client] transient failure (attempt {attempt + 1}/{_RETRY_ATTEMPTS}), "
            f"retrying in {delay:.0f}s: {' '.join(cmd)} :: {reason[:200]}",
            err=True,
        )
        time.sleep(delay)

    def whoami(self) -> dict[str, Any]:
        return self._run(["persona", "whoami"], retryable=True)

    def todos(self) -> dict[str, Any]:
        return self._run(["todos"], retryable=True)

    def work(self) -> dict[str, Any]:
        return self._run(["work", "--notification-category", "wakeable"], retryable=True)

    def topic_progress(self) -> dict[str, Any]:
        return self._run(["topic", "progress"], retryable=True)

    def notifications_unread(
        self,
        *,
        limit: int = 50,
        category: str | None = "wakeable",
    ) -> list[dict[str, Any]]:
        args = ["notification", "list", "--unread-only", "--limit", str(limit), "--format", "yaml"]
        if category is not None:
            args.extend(["--category", category])
        data = self._run(args, retryable=True)
        if not isinstance(data, dict):
            return []
        items = data.get("items")
        return list(items) if isinstance(items, list) else []

    def mention_dismiss(self, mention_id: str) -> dict[str, Any] | None:
        return self._run(["mention", "dismiss", "--id", mention_id])

    def mention_dismiss_all(self) -> dict[str, Any] | None:
        return self._run(["mention", "dismiss-all"])

    def topic_show(self, topic_id: str) -> dict[str, Any]:
        return self._run(["topic", "show", "--id", topic_id], retryable=True)

    def topic_list_open(self) -> list[dict[str, Any]]:
        page = 1
        page_size = 100
        all_rows: list[dict[str, Any]] = []
        while True:
            rows = self._run(
                [
                    "topic",
                    "list",
                    "--status",
                    "open",
                    "--page",
                    str(page),
                    "--page-size",
                    str(page_size),
                    "--format",
                    "yaml",
                ],
                retryable=True,
            )
            page_rows = list(rows or []) if isinstance(rows, list) else []
            all_rows.extend(r for r in page_rows if isinstance(r, dict))
            if len(page_rows) < page_size:
                break
            page += 1
        return all_rows

    def topic_comment(self, topic_id: str, body: str, parent_id: str | None = None) -> dict[str, Any] | None:
        args = ["topic", "comment", "--id", topic_id, "--body", body]
        if parent_id is not None:
            args.extend(["--parent", parent_id])
        return self._run(args)

    def topic_advance_round(self, topic_id: str) -> dict[str, Any] | None:
        return self._run(["topic", "advance-round", "--id", topic_id])

    def topic_resolve(self, topic_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".yaml", delete=True) as fh:
            yaml.safe_dump(payload, fh, allow_unicode=True, sort_keys=False)
            fh.flush()
            return self._run(["topic", "resolve", "--id", topic_id, "--file", fh.name])

    def action_complete(self, action_item_id: str) -> dict[str, Any] | None:
        return self._run(["action", "complete", "--id", action_item_id])

    def action_cancel(
        self,
        action_item_id: str,
        *,
        reason: str,
        category: str | None = None,
    ) -> dict[str, Any] | None:
        args = ["action", "cancel", "--id", action_item_id, "--reason", reason]
        if category is not None:
            args.extend(["--category", category])
        return self._run(args)

    def action_link(self, action_item_id: str, experiment_id: str) -> dict[str, Any] | None:
        return self._run([
            "action", "link",
            "--id", action_item_id,
            "--experiment-id", experiment_id,
        ])

    def action_mark_wake_sent(self, action_item_id: str) -> dict[str, Any] | None:
        """Bump wake_count + stamp last_woken_at + audit (experiment B / I4)."""
        return self._run(["action", "mark-wake-sent", "--id", action_item_id])

    def action_mark_stale(self, action_item_id: str) -> dict[str, Any] | None:
        """Stamp stale_at + write the action_item.stale audit (experiment B / I4)."""
        return self._run(["action", "mark-stale", "--id", action_item_id])

    def experiment_create(
        self,
        title: str,
        plan_file: Path,
        *,
        topic_id: str,
        submit_for_review: bool,
    ) -> dict[str, Any] | None:
        args = [
            "experiment",
            "create",
            "--title",
            title,
            "--plan-file",
            str(plan_file),
            "--topic-id",
            topic_id,
        ]
        if submit_for_review:
            args.append("--submit-for-review")
        return self._run(args)

    def experiment_status(self, experiment_id: str) -> dict[str, Any]:
        return self._run(["experiment", "status", "--id", experiment_id], retryable=True)

    def experiment_list(
        self,
        *,
        phase: str | None = None,
        page_size: int = 100,
    ) -> list[dict[str, Any]]:
        """List experiments (read-only). Used by the E2E driver to discover
        experiment_id from topic_id; not used by waker."""
        args = ["experiment", "list", "--page-size", str(page_size), "--format", "yaml"]
        if phase:
            args.extend(["--phase", phase])
        data = self._run(args, retryable=True)
        return list(data or []) if isinstance(data, list) else []

    def experiment_submit_review(self, experiment_id: str) -> dict[str, Any] | None:
        return self._run(["experiment", "submit-review", "--id", experiment_id])

    def experiment_reviews_list(self, experiment_id: str) -> list[dict[str, Any]]:
        data = self._run(
            ["experiment", "review", "list", "--id", experiment_id, "--format", "yaml"],
            retryable=True,
        )
        return list(data or [])

    def plan_revise(
        self,
        experiment_id: str,
        plan_file: Path,
        *,
        note: str | None,
        addressed_item_ids: list[str],
    ) -> dict[str, Any] | None:
        args = [
            "experiment",
            "plan",
            "revise",
            "--id",
            experiment_id,
            "--plan-file",
            str(plan_file),
        ]
        if note:
            args.extend(["--note", note])
        for item_id in addressed_item_ids:
            args.extend(["--addressed-item", item_id])
        return self._run(args)

    def experiment_approve(self, experiment_id: str) -> dict[str, Any] | None:
        return self._run(["experiment", "approve", "--id", experiment_id])

    def experiment_start(self, experiment_id: str) -> dict[str, Any] | None:
        return self._run(["experiment", "start", "--id", experiment_id])

    def experiment_complete(self, experiment_id: str, *, summary: str, log_file: Path) -> dict[str, Any] | None:
        metadata = {"evidence": {"worker_log": str(log_file)}}
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", encoding="utf-8", delete=False) as fh:
            yaml.safe_dump(metadata, fh, allow_unicode=True, sort_keys=False)
            metadata_file = Path(fh.name)
        try:
            return self._run(
                [
                    "experiment",
                    "complete",
                    "--id",
                    experiment_id,
                    "--summary",
                    summary,
                    "--file",
                    str(log_file),
                    "--metadata",
                    str(metadata_file),
                ]
            )
        finally:
            metadata_file.unlink(missing_ok=True)

    # --- experiment execution lock (CP-3) -------------------------------------

    def experiment_acquire_lock(self, experiment_id: str, *, ttl_seconds: int) -> dict[str, Any] | None:
        return self._run(
            [
                "experiment",
                "lock",
                "acquire",
                "--id",
                experiment_id,
                "--ttl",
                str(ttl_seconds),
            ]
        )

    def experiment_release_lock(self, experiment_id: str) -> dict[str, Any] | None:
        return self._run(["experiment", "lock", "release", "--id", experiment_id])

    def experiment_force_release_lock(
        self, experiment_id: str, *, reason: str, actor: str | None = None
    ) -> dict[str, Any] | None:
        args = [
            "experiment",
            "lock",
            "force-release",
            "--id",
            experiment_id,
            "--reason",
            reason,
        ]
        if actor:
            args.extend(["--actor", actor])
        return self._run(args)

    def experiment_record_skip(
        self, experiment_id: str, *, next_attempt_at: str
    ) -> dict[str, Any] | None:
        return self._run(
            [
                "experiment",
                "lock",
                "skip",
                "--id",
                experiment_id,
                "--next-attempt-at",
                next_attempt_at,
            ]
        )

    def experiment_scan_stalled_locks(self) -> dict[str, Any] | None:
        return self._run(["experiment", "lock", "scan-stalled"])

    # --- inbound event (D6 server gate) ------------------------------------

    def inbound_event_record(
        self,
        *,
        event_id: str,
        fingerprint: str,
        event_type: str,
        source: str = "polling",
    ) -> bool:
        """Record a waker fingerprint against the D6 server gate.

        Returns ``True`` on first-time success, ``False`` if the server already
        had the fingerprint (409 → CLI exit 2). Any other non-zero exit raises
        :class:`WorkerError`. The waker treats ``False`` as "already woken by
        another worker" and skips resume.
        """
        args = [
            "inbound-event",
            "record",
            "--event-id",
            event_id,
            "--fingerprint",
            fingerprint,
            "--event-type",
            event_type,
            "--source",
            source,
        ]
        cmd = self._base_args() + args
        if self.dry_run and _is_write_command(args):
            typer.echo("[dry-run] " + " ".join(cmd))
            return True
        try:
            result = subprocess.run(
                cmd,
                text=True,
                capture_output=True,
                check=False,
                timeout=self.cmd_timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise WorkerError(
                f"Command timed out after {self.cmd_timeout}s: {' '.join(cmd)}"
            ) from exc
        if result.returncode == 0:
            return True
        if result.returncode == 2:
            # CLI maps 409 Conflict to exit 2; treat as duplicate.
            return False
        detail = result.stderr.strip() or result.stdout.strip()
        raise WorkerError(
            f"Command failed ({result.returncode}): {' '.join(cmd)}\n{detail}"
        )


# 会修改 MAP 状态的子命令（``map --dry-run`` 时必须跳过这些，否则会真实
# 写入）。``_WRITE_COMMANDS_2`` 匹配两段路径 ``[group, command]``，
# ``_WRITE_COMMANDS_3`` 匹配三段路径 ``[group, subgroup, command]``。
#
# 这个清单必须与 ``cli/main.py`` 注册的命令保持一致——
# ``tests/cli/test_dry_run_write_commands.py`` 用反射扫描 main.py 的全部
# 命令，强制每个命令要么在此处（写）、要么在测试的 read-only 集合里，
# 从而防止"新增写命令却忘了登记"导致 dry-run 真实执行。
_WRITE_COMMANDS_2: set[tuple[str, str]] = {
    # topic
    ("topic", "comment"),
    ("topic", "create"),
    ("topic", "close"),
    ("topic", "reopen"),
    ("topic", "dismiss"),
    ("topic", "advance-round"),
    ("topic", "rollback-round"),
    ("topic", "resolve"),
    ("topic", "archive"),
    ("topic", "read"),
    ("topic", "mark-seen"),
    # M51D：DB 话题 → map/ 文件夹单向迁移（写本地文件 + archive DB 记录）
    ("topic", "migrate"),
    # experiment
    ("experiment", "create"),
    ("experiment", "submit-review"),
    ("experiment", "approve"),
    ("experiment", "start"),
    ("experiment", "cancel"),
    ("experiment", "complete"),
    ("experiment", "log"),
    ("experiment", "comment"),
    ("experiment", "archive"),
    ("experiment", "accept-result"),
    ("experiment", "reject-result"),
    # mention
    ("mention", "dismiss"),
    ("mention", "dismiss-all"),
    ("mention", "reconcile-stale"),
    # notification（标记已读 = 写）
    ("notification", "read"),
    ("notification", "read-all"),
    # action item
    ("action", "complete"),
    ("action", "cancel"),
    ("action", "deliver"),
    ("action", "link"),
    ("action", "mark-wake-sent"),
    ("action", "mark-stale"),
    # project
    ("project", "create"),
    # inbound event（waker 审计门禁）
    ("inbound-event", "record"),
    # todo 分区清理
    ("todo", "clear"),
    # agent / e2e / host 编排（369ccac 拆分后补登记，此前绕过 dry-run）
    ("agent", "register"),
    ("e2e", "run"),
    ("host", "invoke"),
    # fs plane 验证型写（validate → 本地写回 → commit，走 API）
    ("fs", "advance-round"),
    ("fs", "close"),
    # fs plane 投影上行（PUT /fs/projection，幂等覆盖 server 侧缓存）
    ("fs", "push"),
    ("fs", "sync"),
    # M52C：token 自助轮换（服务端改写 agent.api_token_hash，旧 token 立即失效）
    ("auth", "reissue"),
    # map server bootstrap 会创建项目 + persona（写 MAP 状态）
    ("server", "bootstrap"),
}

_WRITE_COMMANDS_3: set[tuple[str, str, str]] = {
    ("experiment", "plan", "revise"),
    ("experiment", "review", "add"),
    ("experiment", "review", "resolve-item"),
    ("experiment", "review", "withdraw"),
    ("experiment", "lock", "acquire"),
    ("experiment", "lock", "release"),
    ("experiment", "lock", "force-release"),
    ("experiment", "lock", "skip"),
    ("experiment", "lock", "scan-stalled"),
    # project Current Status MD 修订
    ("project", "status", "revise"),
}


def _is_write_command(args: list[str]) -> bool:
    if not args:
        return False
    if len(args) >= 3 and tuple(args[:3]) in _WRITE_COMMANDS_3:
        return True
    return tuple(args[:2]) in _WRITE_COMMANDS_2


# 瞬时失败特征：stderr/stdout 含这些子串时视为可重试（API 5xx / 网络抖动 / 超时）。
# 401/403/404 等确定性错误不在其中——对幂等读命令重试它们只是浪费几次快速失败。
_TRANSIENT_FAILURE_MARKERS: tuple[str, ...] = (
    "5xx", "500", "502", "503", "504",
    "timeout", "timed out",
    "connection", "connect", "reset", "refused", "unreachable",
    "temporarily", "retry", "overloaded",
)

_RETRY_ATTEMPTS = 3
_RETRY_BACKOFF_BASE = 2.0


def _is_transient_failure(detail: str) -> bool:
    lowered = detail.lower()
    return any(marker in lowered for marker in _TRANSIENT_FAILURE_MARKERS)
