from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import typer

from cli.host_experiment_lifecycle import manage_experiment_lifecycle
from cli.host_topic_lifecycle import HostTopicLifecycleMixin
from cli.host_worker_runner import invoke_agent_runner
from cli.host_worker_state import load_host_state, save_host_state
from cli.host_worker_types import MapClientProtocol, WorkerConfig, WorkerError, WorkerStats
from cli.map_command_client import MapCommandClient
from cli.worker_cycle_log import log_cycle_summary

HOST_CYCLE_SUMMARY_FIELDS = [
    "cycles",
    "replies_created",
    "summaries_created",
    "decisions_recorded",
    "experiments_created",
    "plans_revised",
    "experiments_submitted",
    "experiments_approved",
    "experiments_started",
    "experiments_completed",
    "dry_run_actions",
    "runner_invocations",
    "runner_skips",
    "runner_errors",
    "round_advances",
]


class HostWorker(HostTopicLifecycleMixin):
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
            total.add(stats)
            log_cycle_summary("host", total, fields=HOST_CYCLE_SUMMARY_FIELDS)

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
                self._reply_with_template(item, stats)

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

    def _reply_with_template(self, item: dict[str, Any], stats: WorkerStats) -> None:
        body = self._build_reply(item)
        topic_id = str(item["topic_id"])
        parent_id = str(item.get("comment_id")) if item.get("comment_id") else None
        if self.config.dry_run:
            typer.echo(f"[dry-run] would reply topic={topic_id} parent={parent_id}\n{body}")
            stats.dry_run_actions += 1
            return
        self.client.topic_comment(topic_id, body, parent_id=parent_id)
        stats.replies_created += 1

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
        return self._object_state("topics", topic_id)

    def _mark_topic_state(self, topic_id: str, **values: Any) -> None:
        self._mark_object_state("topics", topic_id, **values)

    def _experiment_state(self, experiment_id: str) -> dict[str, Any]:
        return self._object_state("experiments", experiment_id)

    def _mark_experiment_state(self, experiment_id: str, **values: Any) -> None:
        self._mark_object_state("experiments", experiment_id, **values)

    def _object_state(self, collection_name: str, object_id: str) -> dict[str, Any]:
        collection = self.state.setdefault(collection_name, {})
        if not isinstance(collection, dict):
            collection = {}
            self.state[collection_name] = collection
        object_state = collection.setdefault(object_id, {})
        if not isinstance(object_state, dict):
            object_state = {}
            collection[object_id] = object_state
        return object_state

    def _mark_object_state(self, collection_name: str, object_id: str, **values: Any) -> None:
        if self.config.dry_run:
            return
        clean_values = {key: value for key, value in values.items() if value not in (None, "")}
        if not clean_values:
            return
        object_state = self._object_state(collection_name, object_id)
        object_state.update(clean_values)
        object_state["last_action_at"] = datetime.now(UTC).isoformat()
        self._state_dirty = True

    def _save_state_if_needed(self) -> None:
        if self.config.dry_run or not self._state_dirty:
            return
        save_host_state(self.config.state_file, self.state)
        self._state_dirty = False

    def _log_event(self, action: str, topic_id: str, **fields: Any) -> None:
        self._log_json({"action": action, "topic_id": topic_id}, **fields)

    def _log_experiment_event(self, action: str, experiment_id: str, **fields: Any) -> None:
        self._log_json({"action": action, "experiment_id": experiment_id}, **fields)

    def _log_json(self, base: dict[str, Any], **fields: Any) -> None:
        event = {
            **base,
            "dry_run": self.config.dry_run,
            **{key: value for key, value in fields.items() if value not in (None, "")},
        }
        typer.echo(json.dumps(event, ensure_ascii=False, sort_keys=True), err=True)

    def _git_repo(self) -> Path | None:
        if isinstance(self.client, MapCommandClient) and self.client.project_root is not None:
            return self.client.project_root
        return Path.cwd()
