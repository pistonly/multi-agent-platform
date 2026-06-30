from __future__ import annotations

import json
import re
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import typer
import yaml

from cli.bridge_state import load_bridge_state, save_bridge_state
from cli.host_worker_topic import _flatten_comments
from cli.host_worker_types import WorkerError
from cli.map_command_client import MapCommandClient
from cli.worker_cycle_log import log_cycle_summary

PARTICIPANT_CYCLE_SUMMARY_FIELDS = [
    "cycles",
    "comments_created",
    "dry_run_actions",
    "runner_invocations",
    "runner_skips",
    "runner_errors",
    "opportunities_seen",
]

ACTIVE_EXPERIMENT_PHASES = {"draft", "review", "approved", "running"}
ROUND_SUMMARY_RE = re.compile(r"^##\s+Round\s+(\d+)\s+Summary\b", re.IGNORECASE | re.MULTILINE)
ParticipationReason = Literal["initial", "follow_up", "mention"]

DEFAULT_PARTICIPATE_TEMPLATE = """**立场**：愿意参与本话题讨论。

**背景**：{topic_title}
{description_block}

**建议**：请 host 在 Round 1 收齐各方意见后发布 Summary。
"""


class ParticipantMapClient(MapCommandClient):
    def topic_list_open(self) -> list[dict[str, Any]]:
        rows = self._run(["topic", "list", "--status", "open"])
        if rows is None:
            return []
        if isinstance(rows, list):
            return rows
        return []


@dataclass
class ParticipantWorkerConfig:
    interval: float = 30.0
    once: bool = False
    max_cycles: int | None = None
    dry_run: bool = False
    max_actions_per_cycle: int = 1
    agent_runner: str | None = None
    runner_timeout: float = 120.0
    state_file: Path | None = Path(".map/participant-bridge-state.json")
    min_my_comments_before_summary_wait: int = 2
    participate_template: str = DEFAULT_PARTICIPATE_TEMPLATE


@dataclass
class ParticipantWorkerStats:
    cycles: int = 0
    comments_created: int = 0
    dry_run_actions: int = 0
    runner_invocations: int = 0
    runner_skips: int = 0
    runner_errors: int = 0
    opportunities_seen: int = 0


@dataclass
class ParticipationOpportunity:
    topic_id: str
    reason: ParticipationReason
    trigger_id: str
    parent_id: str | None = None
    excerpt: str | None = None
    author_name: str | None = None


@dataclass
class ParticipantWorker:
    client: ParticipantMapClient
    config: ParticipantWorkerConfig = field(default_factory=ParticipantWorkerConfig)
    agent_id: str | None = None
    state: dict[str, Any] = field(default_factory=dict)
    _state_dirty: bool = False

    def __post_init__(self) -> None:
        if not self.state:
            self.state = _load_state(self.config.state_file)

    def run_forever(self) -> ParticipantWorkerStats:
        total = ParticipantWorkerStats()
        while True:
            stats = self.run_once()
            total.cycles += stats.cycles
            total.comments_created += stats.comments_created
            total.dry_run_actions += stats.dry_run_actions
            total.runner_invocations += stats.runner_invocations
            total.runner_skips += stats.runner_skips
            total.runner_errors += stats.runner_errors
            total.opportunities_seen += stats.opportunities_seen
            log_cycle_summary("participant", total, fields=PARTICIPANT_CYCLE_SUMMARY_FIELDS)

            if self.config.once:
                break
            if self.config.max_cycles is not None and total.cycles >= self.config.max_cycles:
                break
            time.sleep(self.config.interval)
        return total

    def run_once(self) -> ParticipantWorkerStats:
        self._ensure_identity()
        stats = ParticipantWorkerStats(cycles=1)
        todos = self.client.todos() or {}
        open_topics = self.client.topic_list_open()
        opportunities = self._discover_opportunities(open_topics, todos)
        stats.opportunities_seen = len(opportunities)

        limit = max(1, self.config.max_actions_per_cycle)
        for opportunity in opportunities[:limit]:
            if self.config.agent_runner:
                self._handle_with_runner(opportunity, stats)
            else:
                self._handle_with_template(opportunity, stats)

        self._save_state_if_needed()
        return stats

    def _ensure_identity(self) -> None:
        if self.agent_id is not None:
            return
        me = self.client.whoami()
        if not me or not me.get("id"):
            raise WorkerError("Could not resolve participant identity; run `map --persona participant persona whoami`")
        self.agent_id = str(me["id"])

    def _discover_opportunities(
        self,
        open_topics: list[dict[str, Any]],
        todos: dict[str, Any],
    ) -> list[ParticipationOpportunity]:
        opportunities: list[ParticipationOpportunity] = []
        seen_keys: set[str] = set()

        for mention in todos.get("mentions") or []:
            topic_id = mention.get("topic_id")
            source_id = mention.get("source_id")
            if not topic_id or not source_id:
                continue
            topic_id = str(topic_id)
            source_id = str(source_id)
            key = f"mention:{topic_id}:{source_id}"
            if key in seen_keys or self._already_handled(topic_id, source_id):
                continue

            topic = self.client.topic_show(topic_id)
            if not topic or str(topic.get("status")) != "open":
                continue
            comments = _flatten_comments(topic.get("comments") or [])
            host_id = str(topic.get("creator_agent_id") or "")
            if _has_direct_reply(comments, self.agent_id, source_id):
                continue
            if self._waiting_for_host_round_summary(topic, comments, host_id=host_id):
                continue

            seen_keys.add(key)
            opportunities.append(
                ParticipationOpportunity(
                    topic_id=topic_id,
                    reason="mention",
                    trigger_id=source_id,
                    parent_id=source_id,
                    excerpt=mention.get("excerpt"),
                    author_name=mention.get("author_name"),
                )
            )

        for summary in open_topics:
            topic_id = str(summary.get("id") or "")
            if not topic_id:
                continue
            topic = self.client.topic_show(topic_id)
            if not topic or str(topic.get("status")) != "open":
                continue
            if _has_active_experiment(topic):
                continue
            if str(topic.get("creator_agent_id") or "") == self.agent_id:
                continue

            comments = _flatten_comments(topic.get("comments") or [])
            my_comments = [c for c in comments if str(c.get("author_agent_id")) == self.agent_id]
            others = [c for c in comments if str(c.get("author_agent_id")) != self.agent_id]

            if not my_comments:
                trigger_id = f"initial:{topic_id}"
                if self._already_handled(topic_id, trigger_id):
                    continue
                key = f"initial:{topic_id}"
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                opportunities.append(
                    ParticipationOpportunity(
                        topic_id=topic_id,
                        reason="initial",
                        trigger_id=trigger_id,
                    )
                )
                continue

            if not others:
                continue

            latest_other = max(others, key=lambda c: str(c.get("created_at") or ""))
            trigger_id = str(latest_other.get("id") or "")
            if not trigger_id or self._already_handled(topic_id, trigger_id):
                continue
            if _has_direct_reply(comments, self.agent_id, trigger_id):
                continue
            if self._waiting_for_host_round_summary(
                topic,
                comments,
                host_id=str(topic.get("creator_agent_id") or ""),
            ):
                continue
            my_latest = max(my_comments, key=lambda c: str(c.get("created_at") or ""))
            if str(latest_other.get("created_at") or "") <= str(my_latest.get("created_at") or ""):
                continue

            key = f"follow_up:{topic_id}:{trigger_id}"
            if key in seen_keys:
                continue
            seen_keys.add(key)
            opportunities.append(
                ParticipationOpportunity(
                    topic_id=topic_id,
                    reason="follow_up",
                    trigger_id=trigger_id,
                    parent_id=trigger_id,
                    excerpt=_excerpt(str(latest_other.get("body") or "")),
                    author_name=latest_other.get("author_name"),
                )
            )

        priority = {"mention": 0, "initial": 1, "follow_up": 2}
        opportunities.sort(key=lambda item: priority[item.reason])
        return opportunities

    def _already_handled(self, topic_id: str, trigger_id: str) -> bool:
        topic_state = self._topic_state(topic_id)
        return topic_state.get("last_handled_trigger_id") == trigger_id

    def _waiting_for_host_round_summary(
        self,
        topic: dict[str, Any],
        comments: list[dict[str, Any]],
        *,
        host_id: str,
    ) -> bool:
        if str(topic.get("discussion_round") or "") != "round1":
            return False
        if int(topic.get("round_summary_count") or 0) > 0:
            return False
        if _host_has_round_summary(comments, host_id, round_n=1):
            return False
        my_count = sum(1 for c in comments if str(c.get("author_agent_id")) == self.agent_id)
        return my_count >= self.config.min_my_comments_before_summary_wait

    def _handle_with_template(self, opportunity: ParticipationOpportunity, stats: ParticipantWorkerStats) -> None:
        topic = self.client.topic_show(opportunity.topic_id)
        body = self._build_template_body(topic, opportunity)
        parent_id = opportunity.parent_id
        if self.config.dry_run:
            typer.echo(
                f"[dry-run] would comment topic={opportunity.topic_id} "
                f"reason={opportunity.reason} parent={parent_id}\n{body}"
            )
            stats.dry_run_actions += 1
        else:
            self.client.topic_comment(opportunity.topic_id, body, parent_id=parent_id)
            stats.comments_created += 1
        self._mark_handled(opportunity)

    def _build_template_body(self, topic: dict[str, Any], opportunity: ParticipationOpportunity) -> str:
        if opportunity.reason == "follow_up" and opportunity.excerpt:
            return (
                f"跟进 @{opportunity.author_name or 'host'} 的观点：\n\n"
                f"- 摘要：{opportunity.excerpt}\n"
                f"- 补充：我会在 Round 2 聚焦未决项继续讨论。"
            )
        description = (topic.get("description") or "").strip()
        description_block = f"\n**描述**：{description}" if description else ""
        return self.config.participate_template.format(
            topic_title=topic.get("title") or opportunity.topic_id,
            description_block=description_block,
        ).strip()

    def _handle_with_runner(self, opportunity: ParticipationOpportunity, stats: ParticipantWorkerStats) -> None:
        topic_id = opportunity.topic_id
        if self._already_handled(topic_id, opportunity.trigger_id):
            stats.runner_skips += 1
            return

        topic = self.client.topic_show(topic_id)
        comments = _flatten_comments(topic.get("comments") or [])
        my_prior = [
            {"id": c.get("id"), "body": c.get("body"), "created_at": c.get("created_at")}
            for c in comments
            if str(c.get("author_agent_id")) == self.agent_id
        ]
        request = {
            "action": "participate",
            "topic_id": topic_id,
            "dry_run": self.config.dry_run,
            "context": {
                "reason": opportunity.reason,
                "topic": {
                    "id": topic.get("id"),
                    "title": topic.get("title"),
                    "description": topic.get("description"),
                    "discussion_round": topic.get("discussion_round"),
                    "round_summary_count": topic.get("round_summary_count"),
                    "creator_name": topic.get("creator_name"),
                },
                "reply_to": {
                    "comment_id": opportunity.parent_id,
                    "excerpt": opportunity.excerpt,
                    "author_name": opportunity.author_name,
                }
                if opportunity.parent_id or opportunity.excerpt
                else None,
                "my_prior_comments": my_prior,
                "idempotency_key": f"{topic_id}:{opportunity.trigger_id}",
            },
        }
        status, result = self._invoke_runner(request, topic_id=topic_id)
        stats.runner_invocations += 1
        if status == "error":
            stats.runner_errors += 1
            return
        if status == "skip":
            stats.runner_skips += 1
            self._mark_handled(opportunity)
            return

        body = result.get("body")
        if not body:
            self._log_event("participate", topic_id, status="runner_empty_body", reason=opportunity.reason)
            stats.runner_errors += 1
            return

        parent_id = result.get("parent_id")
        if parent_id is None:
            parent_id = opportunity.parent_id

        if self.config.dry_run:
            typer.echo(
                f"[dry-run] would comment topic={topic_id} reason={opportunity.reason} parent={parent_id}\n{body}"
            )
            stats.dry_run_actions += 1
        else:
            self.client.topic_comment(topic_id, str(body), parent_id=parent_id)
            stats.comments_created += 1
        self._mark_handled(opportunity)

    def _invoke_runner(self, request: dict[str, Any], *, topic_id: str) -> tuple[str, dict[str, Any]]:
        if not self.config.agent_runner:
            raise WorkerError("agent_runner is not configured")
        cmd = shlex.split(self.config.agent_runner)
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
            self._log_event("participate", topic_id, status="runner_timeout")
            return "error", {}

        if result.returncode == 2:
            self._log_event("participate", topic_id, status="runner_skip", stderr=result.stderr.strip())
            return "skip", {}
        if result.returncode != 0:
            self._log_event(
                "participate",
                topic_id,
                status="runner_error",
                exit_code=result.returncode,
                stderr=result.stderr.strip(),
            )
            return "error", {}

        try:
            parsed = json.loads(result.stdout.strip())
        except json.JSONDecodeError:
            self._log_event("participate", topic_id, status="runner_invalid_json", stdout=result.stdout.strip())
            return "error", {}
        if not isinstance(parsed, dict):
            return "error", {}
        if parsed.get("body") is not None and not isinstance(parsed["body"], str):
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

    def _mark_handled(self, opportunity: ParticipationOpportunity) -> None:
        if self.config.dry_run:
            return
        topic_state = self._topic_state(opportunity.topic_id)
        topic_state["last_handled_trigger_id"] = opportunity.trigger_id
        topic_state["last_reason"] = opportunity.reason
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


def _host_has_round_summary(comments: list[dict[str, Any]], host_id: str, *, round_n: int = 1) -> bool:
    for comment in comments:
        if str(comment.get("author_agent_id")) != host_id:
            continue
        body = str(comment.get("body") or "")
        for match in ROUND_SUMMARY_RE.finditer(body):
            if int(match.group(1)) == round_n:
                return True
    return False


def _has_direct_reply(comments: list[dict[str, Any]], agent_id: str, parent_comment_id: str) -> bool:
    for comment in comments:
        if str(comment.get("author_agent_id")) == agent_id and str(comment.get("parent_comment_id") or "") == parent_comment_id:
            return True
    return False


def _excerpt(body: str, limit: int = 200) -> str:
    text = body.strip().replace("\n", " ")
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _has_active_experiment(topic: dict[str, Any]) -> bool:
    for experiment in topic.get("experiments") or []:
        if experiment.get("archived_at") is not None:
            continue
        if experiment.get("phase") in ACTIVE_EXPERIMENT_PHASES:
            return True
    return False


def _load_state(path: Path | None) -> dict[str, Any]:
    return load_bridge_state(path, bridge_name="participant", default_collections=("topics",))


def _save_state(path: Path | None, state: dict[str, Any]) -> None:
    save_bridge_state(path, state)


def run(
    persona: str = typer.Option("participant", "--persona", "-p"),
    project_root: Path | None = typer.Option(None, "--project-root"),
    map_cmd: str = typer.Option("map", "--map-cmd"),
    interval: float = typer.Option(30.0, "--interval", min=1.0),
    once: bool = typer.Option(False, "--once"),
    max_cycles: int | None = typer.Option(None, "--max-cycles", min=1),
    dry_run: bool = typer.Option(False, "--dry-run"),
    max_actions_per_cycle: int = typer.Option(1, "--max-actions-per-cycle", min=1),
    agent_runner: str | None = typer.Option(None, "--agent-runner"),
    runner_timeout: float = typer.Option(120.0, "--runner-timeout", min=1.0),
    state_file: Path | None = typer.Option(
        Path(".map/participant-bridge-state.json"),
        "--state-file",
    ),
) -> None:
    config = ParticipantWorkerConfig(
        interval=interval,
        once=once,
        max_cycles=max_cycles,
        dry_run=dry_run,
        max_actions_per_cycle=max_actions_per_cycle,
        agent_runner=agent_runner,
        runner_timeout=runner_timeout,
        state_file=state_file,
    )
    client = ParticipantMapClient(
        map_cmd=map_cmd,
        persona=persona,
        project_root=project_root,
        dry_run=dry_run,
    )
    stats = ParticipantWorker(client, config).run_forever()
    typer.echo(yaml.safe_dump(stats.__dict__, allow_unicode=True, sort_keys=False))


def main() -> None:
    typer.run(run)


if __name__ == "__main__":
    main()
