from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import typer
import yaml

from cli.host_worker_topic import (
    _default_plan,
    _flatten_comments,
    _has_active_experiment,
    _host_has_round_summary,
    _host_has_round_summary_in_body,
    _topic_resolution_payload,
)
from cli.host_worker_types import WorkerStats


class HostTopicLifecycleMixin:
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
