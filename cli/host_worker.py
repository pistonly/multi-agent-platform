from __future__ import annotations

import json
import re
import shlex
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import typer
import yaml

DEFAULT_REPLY_TEMPLATE = """已收到这个 thread 的反馈。

- 评论摘要：{excerpt}
- 处理状态：已纳入主持跟进；如需进入实验，我会在本话题完成必要讨论后从 host persona 创建关联实验。
"""

ROUND_SUMMARY_RE = re.compile(r"^##\s+Round\s+(\d+)\s+Summary\b", re.IGNORECASE | re.MULTILINE)
ACTIVE_EXPERIMENT_PHASES = {"draft", "review", "approved", "running"}


class WorkerError(RuntimeError):
    pass


class MapClientProtocol(Protocol):
    def whoami(self) -> dict[str, Any]:
        ...

    def todos(self) -> dict[str, Any]:
        ...

    def topic_show(self, topic_id: str) -> dict[str, Any]:
        ...

    def topic_comment(self, topic_id: str, body: str, parent_id: str | None = None) -> dict[str, Any] | None:
        ...

    def topic_advance_round(self, topic_id: str) -> dict[str, Any] | None:
        ...

    def experiment_create(
        self,
        title: str,
        plan_file: Path,
        *,
        topic_id: str,
        submit_for_review: bool,
    ) -> dict[str, Any] | None:
        ...


@dataclass
class WorkerConfig:
    interval: float = 30.0
    once: bool = False
    max_cycles: int | None = None
    dry_run: bool = False
    reply_template: str = DEFAULT_REPLY_TEMPLATE
    manage_topic_lifecycle: bool = True
    promote_ready_topics: bool = False
    submit_created_experiment_for_review: bool = False
    min_participant_comments: int = 1
    min_round_summaries: int = 2
    max_lifecycle_actions_per_cycle: int = 1
    plan_dir: Path | None = None
    agent_runner: str | None = None
    runner_timeout: float = 120.0
    state_file: Path | None = Path(".map/host-bridge-state.json")


@dataclass
class WorkerStats:
    cycles: int = 0
    replies_created: int = 0
    summaries_created: int = 0
    experiments_created: int = 0
    dry_run_actions: int = 0
    runner_invocations: int = 0
    runner_skips: int = 0
    runner_errors: int = 0
    round_advances: int = 0


class MapCommandClient:
    def __init__(
        self,
        *,
        map_cmd: str = "map",
        persona: str = "host",
        project_root: Path | None = None,
        dry_run: bool = False,
    ) -> None:
        self.map_cmd = map_cmd
        self.persona = persona
        self.project_root = project_root
        self.dry_run = dry_run

    def _base_args(self) -> list[str]:
        args = [self.map_cmd, "--persona", self.persona]
        if self.project_root is not None:
            args.extend(["--project-root", str(self.project_root)])
        return args

    def _run(self, args: list[str], *, parse_yaml: bool = True) -> Any:
        cmd = self._base_args() + args
        if self.dry_run and _is_write_command(args):
            typer.echo("[dry-run] " + " ".join(cmd))
            return None
        result = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise WorkerError(f"Command failed ({result.returncode}): {' '.join(cmd)}\n{detail}")
        if not parse_yaml:
            return result.stdout
        if not result.stdout.strip():
            return None
        return yaml.safe_load(result.stdout)

    def whoami(self) -> dict[str, Any]:
        return self._run(["persona", "whoami"])

    def todos(self) -> dict[str, Any]:
        return self._run(["todos"])

    def topic_show(self, topic_id: str) -> dict[str, Any]:
        return self._run(["topic", "show", "--id", topic_id])

    def topic_comment(self, topic_id: str, body: str, parent_id: str | None = None) -> dict[str, Any] | None:
        args = ["topic", "comment", "--id", topic_id, "--body", body]
        if parent_id is not None:
            args.extend(["--parent", parent_id])
        return self._run(args)

    def topic_advance_round(self, topic_id: str) -> dict[str, Any] | None:
        return self._run(["topic", "advance-round", "--id", topic_id])

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


class HostWorker:
    def __init__(self, client: MapClientProtocol, config: WorkerConfig | None = None) -> None:
        self.client = client
        self.config = config or WorkerConfig()
        self.host_id: str | None = None
        self.state: dict[str, Any] = _load_state(self.config.state_file)
        self._state_dirty = False

    def run_forever(self) -> WorkerStats:
        total = WorkerStats()
        while True:
            stats = self.run_once()
            total.cycles += stats.cycles
            total.replies_created += stats.replies_created
            total.summaries_created += stats.summaries_created
            total.experiments_created += stats.experiments_created
            total.dry_run_actions += stats.dry_run_actions
            total.runner_invocations += stats.runner_invocations
            total.runner_skips += stats.runner_skips
            total.runner_errors += stats.runner_errors
            total.round_advances += stats.round_advances

            if self.config.once:
                break
            if self.config.max_cycles is not None and total.cycles >= self.config.max_cycles:
                break
            time.sleep(self.config.interval)
        return total

    def run_once(self) -> WorkerStats:
        self._ensure_identity()
        stats = WorkerStats(cycles=1)
        todos = self.client.todos() or {}
        pending = list(todos.get("pending_topic_replies") or [])
        pending_topic_ids = {str(item["topic_id"]) for item in pending if item.get("topic_id")}

        for item in pending:
            if self.config.agent_runner:
                self._handle_pending_with_runner(item, stats)
            else:
                body = self._build_reply(item)
                topic_id = str(item["topic_id"])
                parent_id = str(item.get("comment_id")) if item.get("comment_id") else None
                if self.config.dry_run:
                    typer.echo(f"[dry-run] would reply topic={topic_id} parent={parent_id}\n{body}")
                    stats.dry_run_actions += 1
                else:
                    self.client.topic_comment(topic_id, body, parent_id=parent_id)
                    stats.replies_created += 1

        if self.config.manage_topic_lifecycle and self.config.agent_runner:
            self._manage_topic_lifecycle(todos, pending_topic_ids, stats)
        elif self.config.promote_ready_topics:
            promoted = self._promote_ready_topics(todos, pending, stats)
            if self.config.dry_run:
                stats.dry_run_actions += promoted
            else:
                stats.experiments_created += promoted

        self._save_state_if_needed()
        return stats

    def _ensure_identity(self) -> None:
        if self.host_id is not None:
            return
        me = self.client.whoami()
        if not me or not me.get("id"):
            raise WorkerError("Could not resolve host identity; run `map --persona host persona whoami` first")
        self.host_id = str(me["id"])

    def _build_reply(self, item: dict[str, Any]) -> str:
        values = {
            "topic_id": item.get("topic_id", ""),
            "topic_title": item.get("topic_title", ""),
            "comment_id": item.get("comment_id", ""),
            "thread_root_id": item.get("thread_root_id", ""),
            "author_agent_id": item.get("author_agent_id", ""),
            "author_name": item.get("author_name") or "unknown",
            "excerpt": item.get("excerpt") or "",
        }
        return self.config.reply_template.format(**values).strip()

    def _handle_pending_with_runner(self, item: dict[str, Any], stats: WorkerStats) -> None:
        topic_id = str(item["topic_id"])
        comment_id = str(item.get("comment_id") or "")
        if comment_id and self._topic_state(topic_id).get("last_handled_comment_id") == comment_id:
            self._log_event(
                "reply_pending",
                topic_id,
                status="skip_seen",
                idempotency_key=f"{topic_id}:{comment_id}",
            )
            stats.runner_skips += 1
            return

        topic = self.client.topic_show(topic_id)
        idempotency_key = f"{topic_id}:{comment_id or item.get('thread_root_id', '')}"
        request = {
            "action": "reply_pending",
            "topic_id": topic_id,
            "dry_run": self.config.dry_run,
            "context": {
                "pending_item": item,
                "topic": {
                    "id": topic.get("id"),
                    "title": topic.get("title"),
                    "discussion_round": topic.get("discussion_round"),
                    "round_summary_count": topic.get("round_summary_count"),
                },
                "idempotency_key": idempotency_key,
            },
        }
        status, result = self._invoke_runner(request, action="reply_pending", topic_id=topic_id)
        stats.runner_invocations += 1
        if status == "error":
            stats.runner_errors += 1
            return
        if status == "skip":
            stats.runner_skips += 1
            self._mark_topic_state(topic_id, last_handled_comment_id=comment_id)
            return

        wrote = self._apply_runner_result(
            topic_id,
            topic,
            result,
            stats,
            default_parent_id=comment_id or None,
            mark_comment_id=comment_id,
        )
        if not wrote:
            self._mark_topic_state(topic_id, last_handled_comment_id=comment_id)

    def _manage_topic_lifecycle(
        self,
        todos: dict[str, Any],
        pending_topic_ids: set[str],
        stats: WorkerStats,
    ) -> None:
        actions = 0
        max_actions = self.config.max_lifecycle_actions_per_cycle
        for topic_summary in todos.get("my_open_topics") or []:
            if actions >= max_actions:
                break
            topic_id = str(topic_summary["id"])
            if topic_id in pending_topic_ids:
                continue
            topic = self.client.topic_show(topic_id)

            if self._topic_needs_advance_round(topic):
                if self.config.dry_run:
                    typer.echo(f"[dry-run] would advance-round topic={topic_id}")
                    stats.dry_run_actions += 1
                else:
                    self.client.topic_advance_round(topic_id)
                    stats.round_advances += 1
                actions += 1
                continue

            round_n = self._topic_needs_round_summary(topic, pending_topic_ids)
            if round_n is not None:
                if self._handle_round_summary_with_runner(topic, stats, round_n=round_n):
                    actions += 1
                continue

            if self._should_promote_topic(topic, pending_topic_ids):
                promoted = self._promote_with_runner(topic, stats)
                if promoted:
                    stats.experiments_created += promoted
                    actions += promoted

    def _should_promote_topic(self, topic: dict[str, Any], pending_topic_ids: set[str]) -> bool:
        topic_id = str(topic["id"])
        if topic_id in pending_topic_ids:
            return False
        if self.config.manage_topic_lifecycle:
            return self._topic_ready_for_experiment(topic)
        return self.config.promote_ready_topics and self._topic_ready_for_experiment(topic)

    def _topic_needs_round_summary(self, topic: dict[str, Any], pending_topic_ids: set[str]) -> int | None:
        topic_id = str(topic["id"])
        if topic_id in pending_topic_ids:
            return None
        if _has_active_experiment(topic):
            return None
        discussion_round = topic.get("discussion_round")
        if discussion_round not in ("round1", "round2"):
            return None

        comments = _flatten_comments(topic.get("comments") or [])
        participant_comments = [c for c in comments if str(c.get("author_agent_id")) != self.host_id]
        if len(participant_comments) < self.config.min_participant_comments:
            return None

        round_summary_count = int(topic.get("round_summary_count") or 0)
        if discussion_round == "round1" and round_summary_count == 0:
            if not _host_has_round_summary(comments, self.host_id, round_n=1):
                return 1
        elif discussion_round == "round2" and round_summary_count == 1:
            if not _host_has_round_summary(comments, self.host_id, round_n=2):
                return 2
        return None

    def _topic_needs_advance_round(self, topic: dict[str, Any]) -> bool:
        discussion_round = topic.get("discussion_round")
        if discussion_round not in ("round1", "round2"):
            return False
        round_summary_count = int(topic.get("round_summary_count") or 0)
        comments = _flatten_comments(topic.get("comments") or [])
        if discussion_round == "round1" and round_summary_count == 0:
            return _host_has_round_summary(comments, self.host_id, round_n=1)
        if discussion_round == "round2" and round_summary_count == 1:
            return _host_has_round_summary(comments, self.host_id, round_n=2)
        return False

    def _handle_round_summary_with_runner(self, topic: dict[str, Any], stats: WorkerStats, *, round_n: int) -> bool:
        topic_id = str(topic["id"])
        if self._topic_state(topic_id).get("last_posted_summary_round") == round_n:
            self._log_event("round_summary", topic_id, status="skip_seen", round_n=round_n)
            stats.runner_skips += 1
            return False

        comments = _flatten_comments(topic.get("comments") or [])
        participant_comments = [
            {
                "id": c.get("id"),
                "author_name": c.get("author_name"),
                "body": c.get("body"),
                "created_at": c.get("created_at"),
            }
            for c in comments
            if str(c.get("author_agent_id")) != self.host_id
        ]
        request = {
            "action": "round_summary",
            "topic_id": topic_id,
            "dry_run": self.config.dry_run,
            "context": {
                "round_n": round_n,
                "topic": {
                    "id": topic.get("id"),
                    "title": topic.get("title"),
                    "description": topic.get("description"),
                    "discussion_round": topic.get("discussion_round"),
                    "round_summary_count": topic.get("round_summary_count"),
                },
                "participant_comments": participant_comments,
                "idempotency_key": f"{topic_id}:summary:{round_n}",
            },
        }
        status, result = self._invoke_runner(request, action="round_summary", topic_id=topic_id)
        stats.runner_invocations += 1
        if status == "error":
            stats.runner_errors += 1
            return False
        if status == "skip":
            stats.runner_skips += 1
            self._mark_topic_state(topic_id, last_posted_summary_round=round_n)
            return False

        if result.get("advance_round") is None:
            result["advance_round"] = True
        wrote = self._apply_runner_result(
            topic_id,
            topic,
            result,
            stats,
            default_parent_id=None,
            mark_summary_round=round_n,
            count_as_summary=True,
        )
        return wrote

    def _apply_runner_result(
        self,
        topic_id: str,
        topic: dict[str, Any],
        result: dict[str, Any],
        stats: WorkerStats,
        *,
        default_parent_id: str | None,
        mark_comment_id: str | None = None,
        mark_summary_round: int | None = None,
        count_as_summary: bool = False,
    ) -> bool:
        body = result.get("body")
        wrote = False
        if body:
            parent_id = result.get("parent_id")
            if parent_id is None:
                parent_id = default_parent_id
            if self.config.dry_run:
                typer.echo(f"[dry-run] would comment topic={topic_id} parent={parent_id}\n{body}")
                stats.dry_run_actions += 1
            else:
                self.client.topic_comment(topic_id, str(body), parent_id=parent_id)
                if count_as_summary:
                    stats.summaries_created += 1
                else:
                    stats.replies_created += 1
            wrote = True

        if result.get("advance_round"):
            if self.config.dry_run:
                typer.echo(f"[dry-run] would advance-round topic={topic_id}")
                stats.dry_run_actions += 1
            else:
                self.client.topic_advance_round(topic_id)
                stats.round_advances += 1
            wrote = True

        if result.get("create_experiment"):
            if self._topic_ready_for_experiment(topic):
                self._create_experiment_from_topic(topic, plan_content=str(body) if body else None)
                if self.config.dry_run:
                    stats.dry_run_actions += 1
                else:
                    stats.experiments_created += 1
                wrote = True
            else:
                self._log_event("promote_experiment", topic_id, status="skip_not_ready")

        if wrote:
            state_updates: dict[str, Any] = {}
            if mark_comment_id:
                state_updates["last_handled_comment_id"] = mark_comment_id
            if mark_summary_round is not None:
                state_updates["last_posted_summary_round"] = mark_summary_round
            self._mark_topic_state(topic_id, **state_updates)
        return wrote

    def _promote_ready_topics(
        self,
        todos: dict[str, Any],
        pending: list[dict[str, Any]],
        stats: WorkerStats,
    ) -> int:
        pending_topic_ids = {str(item["topic_id"]) for item in pending if item.get("topic_id")}
        count = 0
        for topic_summary in todos.get("my_open_topics") or []:
            topic_id = str(topic_summary["id"])
            if topic_id in pending_topic_ids:
                continue
            topic = self.client.topic_show(topic_id)
            if not self._topic_ready_for_experiment(topic):
                continue
            if self.config.agent_runner:
                count += self._promote_with_runner(topic, stats)
            else:
                self._create_experiment_from_topic(topic)
                count += 1
        return count

    def _promote_with_runner(self, topic: dict[str, Any], stats: WorkerStats) -> int:
        topic_id = str(topic["id"])
        round_count = int(topic.get("round_summary_count") or 0)
        if self._topic_state(topic_id).get("last_round_summary_count") == round_count:
            self._log_event("promote_experiment", topic_id, status="skip_seen")
            stats.runner_skips += 1
            return 0

        request = {
            "action": "promote_experiment",
            "topic_id": topic_id,
            "dry_run": self.config.dry_run,
            "context": {
                "topic": {
                    "id": topic.get("id"),
                    "title": topic.get("title"),
                    "discussion_round": topic.get("discussion_round"),
                    "round_summary_count": topic.get("round_summary_count"),
                },
                "idempotency_key": f"{topic_id}:promote:{round_count}",
            },
        }
        status, result = self._invoke_runner(request, action="promote_experiment", topic_id=topic_id)
        stats.runner_invocations += 1
        if status == "error":
            stats.runner_errors += 1
            return 0
        if status == "skip":
            stats.runner_skips += 1
            self._mark_topic_state(topic_id, last_round_summary_count=round_count)
            return 0
        if not result.get("create_experiment"):
            self._log_event("promote_experiment", topic_id, status="skip_runner_declined")
            return 0

        self._create_experiment_from_topic(topic, plan_content=result.get("body") or None)
        self._mark_topic_state(topic_id, last_round_summary_count=round_count)
        return 1

    def _topic_ready_for_experiment(self, topic: dict[str, Any]) -> bool:
        if _has_active_experiment(topic):
            return False
        comments = _flatten_comments(topic.get("comments") or [])
        participant_comments = [c for c in comments if str(c.get("author_agent_id")) != self.host_id]
        if len(participant_comments) < self.config.min_participant_comments:
            return False

        discussion_round = topic.get("discussion_round")
        round_summary_count = int(topic.get("round_summary_count") or 0)
        if discussion_round is not None and (discussion_round != "round1" or round_summary_count > 0):
            return discussion_round == "ready" and round_summary_count >= self.config.min_round_summaries

        round_summaries = [
            c
            for c in comments
            if str(c.get("author_agent_id")) == self.host_id and _host_has_round_summary_in_body(c.get("body") or "")
        ]
        return len(round_summaries) >= self.config.min_round_summaries

    def _create_experiment_from_topic(self, topic: dict[str, Any], plan_content: str | None = None) -> None:
        topic_id = str(topic["id"])
        title = f"实验：{topic.get('title') or topic_id}"
        plan_content = plan_content or _default_plan(topic)
        if self.config.dry_run:
            typer.echo(f"[dry-run] would create experiment topic={topic_id} title={title}\n{plan_content}")
            return

        plan_dir = self.config.plan_dir
        if plan_dir is not None:
            plan_dir.mkdir(parents=True, exist_ok=True)
            plan_file = plan_dir / f"topic-{topic_id}-plan.md"
            plan_file.write_text(plan_content, encoding="utf-8")
            self.client.experiment_create(
                title,
                plan_file,
                topic_id=topic_id,
                submit_for_review=self.config.submit_created_experiment_for_review,
            )
            return

        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=True) as fh:
            fh.write(plan_content)
            fh.flush()
            self.client.experiment_create(
                title,
                Path(fh.name),
                topic_id=topic_id,
                submit_for_review=self.config.submit_created_experiment_for_review,
            )

    def _invoke_runner(
        self,
        request: dict[str, Any],
        *,
        action: str,
        topic_id: str,
    ) -> tuple[str, dict[str, Any]]:
        if not self.config.agent_runner:
            raise WorkerError("agent_runner is not configured")
        cmd = shlex.split(self.config.agent_runner)
        if not cmd:
            raise WorkerError("agent_runner is empty")

        payload = json.dumps(request, ensure_ascii=False, separators=(",", ":")) + "\n"
        try:
            result = subprocess.run(
                cmd,
                input=payload,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.config.runner_timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            self._log_event(action, topic_id, status="runner_timeout")
            return "error", {}

        if result.returncode == 2:
            self._log_event(action, topic_id, status="runner_skip", stderr=result.stderr.strip())
            return "skip", {}
        if result.returncode != 0:
            self._log_event(
                action,
                topic_id,
                status="runner_error",
                exit_code=result.returncode,
                stderr=result.stderr.strip(),
            )
            return "error", {}

        try:
            parsed = json.loads(result.stdout.strip())
        except json.JSONDecodeError:
            self._log_event(action, topic_id, status="runner_invalid_json", stdout=result.stdout.strip())
            return "error", {}
        if not isinstance(parsed, dict):
            self._log_event(action, topic_id, status="runner_invalid_payload")
            return "error", {}

        for key in ("advance_round", "create_experiment"):
            if key in parsed:
                parsed[key] = bool(parsed[key])
        if parsed.get("body") is not None and not isinstance(parsed["body"], str):
            self._log_event(action, topic_id, status="runner_invalid_body")
            return "error", {}
        if parsed.get("parent_id") is not None:
            parsed["parent_id"] = str(parsed["parent_id"])
        return "ok", parsed

    def _topic_state(self, topic_id: str) -> dict[str, Any]:
        topics = self.state.setdefault("topics", {})
        if not isinstance(topics, dict):
            topics = {}
            self.state["topics"] = topics
        topic_state = topics.setdefault(topic_id, {})
        if not isinstance(topic_state, dict):
            topic_state = {}
            topics[topic_id] = topic_state
        return topic_state

    def _mark_topic_state(self, topic_id: str, **values: Any) -> None:
        if self.config.dry_run:
            return
        clean_values = {key: value for key, value in values.items() if value not in (None, "")}
        if not clean_values:
            return
        topic_state = self._topic_state(topic_id)
        topic_state.update(clean_values)
        topic_state["last_action_at"] = datetime.now(UTC).isoformat()
        self._state_dirty = True

    def _save_state_if_needed(self) -> None:
        if self.config.dry_run or not self._state_dirty:
            return
        _save_state(self.config.state_file, self.state)
        self._state_dirty = False

    def _log_event(self, action: str, topic_id: str, **fields: Any) -> None:
        event = {
            "action": action,
            "topic_id": topic_id,
            "dry_run": self.config.dry_run,
            **{key: value for key, value in fields.items() if value not in (None, "")},
        }
        typer.echo(json.dumps(event, ensure_ascii=False, sort_keys=True), err=True)


def _is_write_command(args: list[str]) -> bool:
    if not args:
        return False
    if args[:2] in (
        ["topic", "comment"],
        ["topic", "create"],
        ["topic", "close"],
        ["topic", "reopen"],
        ["topic", "advance-round"],
        ["experiment", "create"],
        ["experiment", "submit-review"],
        ["experiment", "approve"],
        ["experiment", "start"],
        ["experiment", "complete"],
        ["experiment", "log"],
    ):
        return True
    if args[:3] == ["experiment", "plan", "revise"]:
        return True
    if args[:3] == ["experiment", "review", "add"]:
        return True
    return False


def _load_state(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {"schema_version": 1, "topics": {}}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise WorkerError(f"Invalid host bridge state file: {path}") from exc
    if not isinstance(state, dict):
        raise WorkerError(f"Invalid host bridge state file: {path}")
    if state.get("schema_version", 1) != 1:
        raise WorkerError(f"Unsupported host bridge state schema: {state.get('schema_version')}")
    state.setdefault("schema_version", 1)
    state.setdefault("topics", {})
    return state


def _save_state(path: Path | None, state: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp_path.replace(path)


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


def run(
    persona: str = typer.Option("host", "--persona", "-p", help="Persona used by the worker."),
    project_root: Path | None = typer.Option(None, "--project-root", help="Repo root containing .map/."),
    map_cmd: str = typer.Option("map", "--map-cmd", help="MAP CLI executable."),
    interval: float = typer.Option(30.0, "--interval", min=1.0, help="Polling interval in seconds."),
    once: bool = typer.Option(False, "--once", help="Run one cycle and exit."),
    max_cycles: int | None = typer.Option(None, "--max-cycles", min=1, help="Stop after N cycles."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print intended writes without executing them."),
    reply_template_file: Path | None = typer.Option(
        None,
        "--reply-template-file",
        help="Markdown template for replies. Supports {topic_id}, {topic_title}, {comment_id}, {author_name}, {excerpt}.",
    ),
    promote_ready_topics: bool = typer.Option(
        False,
        "--promote-ready-topics",
        help="Create experiments for open topics that satisfy the readiness gate (also on when --manage-topic-lifecycle).",
    ),
    manage_topic_lifecycle: bool = typer.Option(
        True,
        "--manage-topic-lifecycle/--no-manage-topic-lifecycle",
        help="When agent-runner is set: post Round Summaries, advance rounds, and promote ready topics.",
    ),
    max_lifecycle_actions: int = typer.Option(
        1,
        "--max-lifecycle-actions",
        min=1,
        help="Max round-summary/advance/promote actions per cycle when lifecycle is enabled.",
    ),
    submit_for_review: bool = typer.Option(
        False,
        "--submit-for-review",
        help="Submit newly created experiments for review.",
    ),
    min_participant_comments: int = typer.Option(1, "--min-participant-comments", min=1),
    min_round_summaries: int = typer.Option(2, "--min-round-summaries", min=0),
    plan_dir: Path | None = typer.Option(None, "--plan-dir", help="Persist generated plan files in this directory."),
    agent_runner: str | None = typer.Option(
        None,
        "--agent-runner",
        help="External command that receives one JSON request on stdin and returns one JSON response on stdout.",
    ),
    runner_timeout: float = typer.Option(120.0, "--runner-timeout", min=1.0, help="Agent runner timeout in seconds."),
    state_file: Path | None = typer.Option(
        Path(".map/host-bridge-state.json"),
        "--state-file",
        help="Local idempotency state file for bridge mode.",
    ),
) -> None:
    reply_template = (
        reply_template_file.read_text(encoding="utf-8") if reply_template_file is not None else DEFAULT_REPLY_TEMPLATE
    )
    config = WorkerConfig(
        interval=interval,
        once=once,
        max_cycles=max_cycles,
        dry_run=dry_run,
        reply_template=reply_template,
        promote_ready_topics=promote_ready_topics,
        manage_topic_lifecycle=manage_topic_lifecycle,
        max_lifecycle_actions_per_cycle=max_lifecycle_actions,
        submit_created_experiment_for_review=submit_for_review,
        min_participant_comments=min_participant_comments,
        min_round_summaries=min_round_summaries,
        plan_dir=plan_dir,
        agent_runner=agent_runner,
        runner_timeout=runner_timeout,
        state_file=state_file,
    )
    client = MapCommandClient(map_cmd=map_cmd, persona=persona, project_root=project_root, dry_run=dry_run)
    stats = HostWorker(client, config).run_forever()
    typer.echo(yaml.safe_dump(stats.__dict__, allow_unicode=True, sort_keys=False))


def main() -> None:
    typer.run(run)


if __name__ == "__main__":
    main()
