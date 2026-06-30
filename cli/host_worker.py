from __future__ import annotations

from pathlib import Path

import typer
import yaml

from cli.host_worker_core import HostWorker
from cli.host_worker_state import load_host_state as _load_state
from cli.host_worker_state import save_host_state as _save_state
from cli.host_worker_topic import (
    ACTIVE_EXPERIMENT_PHASES,
    ROUND_SUMMARY_RE,
    _clean_text,
    _clean_uuid_text,
    _default_plan,
    _flatten_comments,
    _has_active_experiment,
    _host_has_round_summary,
    _host_has_round_summary_in_body,
    _normalize_action_items,
    _topic_resolution_payload,
)
from cli.host_worker_types import DEFAULT_REPLY_TEMPLATE, MapClientProtocol, WorkerConfig, WorkerError, WorkerStats
from cli.map_command_client import MapCommandClient, _is_write_command

__all__ = [
    "ACTIVE_EXPERIMENT_PHASES",
    "DEFAULT_REPLY_TEMPLATE",
    "HostWorker",
    "MapClientProtocol",
    "MapCommandClient",
    "ROUND_SUMMARY_RE",
    "WorkerConfig",
    "WorkerError",
    "WorkerStats",
    "_clean_text",
    "_clean_uuid_text",
    "_default_plan",
    "_flatten_comments",
    "_has_active_experiment",
    "_host_has_round_summary",
    "_host_has_round_summary_in_body",
    "_is_write_command",
    "_load_state",
    "_normalize_action_items",
    "_save_state",
    "_topic_resolution_payload",
]


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
    auto_experiment_lifecycle: bool = typer.Option(
        True,
        "--auto-experiment-lifecycle/--no-auto-experiment-lifecycle",
        help="Auto revise/approve/start/execute/complete host experiments.",
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
        auto_experiment_lifecycle=auto_experiment_lifecycle,
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
