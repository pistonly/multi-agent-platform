from __future__ import annotations

import json
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import typer
import yaml

from cli.host_experiment_lifecycle import manage_experiment_lifecycle
from cli.host_worker_runner import invoke_agent_runner
from cli.host_worker_state import load_host_state, save_host_state
from cli.host_worker_topic import (
    _default_plan,
    _flatten_comments,
    _has_active_experiment,
    _host_has_round_summary,
    _host_has_round_summary_in_body,
    _topic_resolution_payload,
)
from cli.host_worker_types import MapClientProtocol, WorkerConfig, WorkerError, WorkerStats
from cli.map_command_client import MapCommandClient


class HostWorker:
    def __init__(self, client: MapClientProtocol, config: WorkerConfig | None = None) -> None:
        self.client = client
        self.config = config or WorkerConfig()
        self.host_id: str | None = None
        self.state: dict[str, Any] = load_host_state(self.config.state_file)
        self._state_dirty = False

    def run_forever(self) -> WorkerStats:
        total = WorkerStats()
        while True:
            stats = self.run_once()
            total.cycles += stats.cycles
            total.replies_created += stats.replies_created
            total.summaries_created += stats.summaries_created
            total.decisions_recorded += stats.decisions_recorded
            total.experiments_created += stats.experiments_created
            total.plans_revised += stats.plans_revised
            total.experiments_approved += stats.experiments_approved
            total.experiments_started += stats.experiments_started
            total.experiments_completed += stats.experiments_completed
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
            manage_experiment_lifecycle(self, todos, stats)
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
                    if self.config.dry_run:
                        stats.dry_run_actions += promoted
                    else:
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
            if result.get("decision") or result.get("no_decision_reason"):
                self._resolve_topic_from_runner_result(topic, result, stats, round_count=round_count)
            self._log_event("promote_experiment", topic_id, status="skip_runner_declined")
            return 0

        self._resolve_topic_from_runner_result(topic, result, stats, round_count=round_count)
        self._create_experiment_from_topic(topic, plan_content=result.get("body") or None)
        self._mark_topic_state(topic_id, last_round_summary_count=round_count)
        return 1

    def _resolve_topic_from_runner_result(
        self,
        topic: dict[str, Any],
        result: dict[str, Any],
        stats: WorkerStats,
        *,
        round_count: int,
    ) -> bool:
        topic_id = str(topic["id"])
        if self._topic_state(topic_id).get("last_resolved_round_summary_count") == round_count:
            self._log_event("topic_resolve", topic_id, status="skip_seen", round_summary_count=round_count)
            return False

        payload = _topic_resolution_payload(topic, result)
        if payload is None:
            return False

        if self.config.dry_run:
            typer.echo(
                f"[dry-run] would resolve topic={topic_id}\n"
                f"{yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)}"
            )
            stats.dry_run_actions += 1
            return True

        self.client.topic_resolve(topic_id, payload)
        stats.decisions_recorded += 1
        self._mark_topic_state(topic_id, last_resolved_round_summary_count=round_count)
        return True

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
        return invoke_agent_runner(
            agent_runner=self.config.agent_runner,
            runner_timeout=self.config.runner_timeout,
            request=request,
            action=action,
            topic_id=topic_id,
            log_event=self._log_event,
        )

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
        save_host_state(self.config.state_file, self.state)
        self._state_dirty = False

    def _log_event(self, action: str, topic_id: str, **fields: Any) -> None:
        event = {
            "action": action,
            "topic_id": topic_id,
            "dry_run": self.config.dry_run,
            **{key: value for key, value in fields.items() if value not in (None, "")},
        }
        typer.echo(json.dumps(event, ensure_ascii=False, sort_keys=True), err=True)

    def _experiment_state(self, experiment_id: str) -> dict[str, Any]:
        experiments = self.state.setdefault("experiments", {})
        if not isinstance(experiments, dict):
            experiments = {}
            self.state["experiments"] = experiments
        exp_state = experiments.setdefault(experiment_id, {})
        if not isinstance(exp_state, dict):
            exp_state = {}
            experiments[experiment_id] = exp_state
        return exp_state

    def _mark_experiment_state(self, experiment_id: str, **values: Any) -> None:
        if self.config.dry_run:
            return
        clean_values = {key: value for key, value in values.items() if value not in (None, "")}
        if not clean_values:
            return
        exp_state = self._experiment_state(experiment_id)
        exp_state.update(clean_values)
        exp_state["last_action_at"] = datetime.now(UTC).isoformat()
        self._state_dirty = True

    def _git_repo(self) -> Path | None:
        if isinstance(self.client, MapCommandClient) and self.client.project_root is not None:
            return self.client.project_root
        return Path.cwd()

    def _log_experiment_event(self, action: str, experiment_id: str, **fields: Any) -> None:
        event = {
            "action": action,
            "experiment_id": experiment_id,
            "dry_run": self.config.dry_run,
            **{key: value for key, value in fields.items() if value not in (None, "")},
        }
        typer.echo(json.dumps(event, ensure_ascii=False, sort_keys=True), err=True)
