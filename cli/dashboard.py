"""T33: ``map dashboard`` implementation (extracted from ``cli/main.py``).

The ~130-line rendering body used to live inline in the ``dashboard``
command; the command stub stays in ``cli.main`` and delegates here.
Runtime state (``_client_ctx`` / ``_cli_options``) is resolved through
the ``cli.main`` module object at call time — same injection surface as
``cli.runner`` (T23), so monkeypatching ``cli.main._client_ctx`` keeps
affecting this renderer. ``cli.main`` does NOT import this module at
top level (lazy import inside the command stub), so there is no cycle.
"""

from __future__ import annotations

import typer
from map_client.exceptions import MAPHTTPError

from cli import main as _main  # runtime state (injection surface)
from cli.runner import _emit_maphttp_error, _resolve_project


def render_dashboard() -> None:
    """One-glance markdown overview: identity, open topics, experiments, todos.

    Aggregates multiple read-only API calls into a single human-friendly
    markdown snapshot. Unlike ``status`` (which returns the server's
    status_md narrative) or ``work`` (which returns structured YAML for
    wakers), ``dashboard`` renders a compact, scannable view designed for
    humans who want to see "what's going on" without running several
    commands.

    Data is fetched live on each invocation — no stale snapshots.
    """
    from datetime import datetime

    from map_types import ExperimentPhase, TopicStatus

    try:
        ctx = _main._client_ctx()
        with ctx as client:
            me = client.get_me()
            project_id = _resolve_project(client, None, None)
            open_topics = client.list_topics(project_id, status=TopicStatus.open)
            experiments = client.list_experiments(project_id)
            todos = client.get_todos()
    except MAPHTTPError as exc:
        _emit_maphttp_error(
            exc, experiment_id=None, output_format=_main._cli_options.get("format", "yaml")
        )
        raise typer.Exit(1) from exc
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    persona = _main._cli_options.get("persona") or "default"
    lines: list[str] = []
    lines.append(f"# MAP Dashboard — {me.name}")
    lines.append(f"_Generated: {now} (persona: {persona})_")
    lines.append("")

    # --- Open Topics ---
    lines.append(f"## Open Topics ({len(open_topics)})")
    if open_topics:
        lines.append("| # | Title | Round | Comments | Experiments | Creator | Created |")
        lines.append("|---|-------|-------|----------|-------------|---------|---------|")
        for i, t in enumerate(open_topics, 1):
            title = (t.title or "").replace("|", "\\|")
            if len(title) > 60:
                title = title[:57] + "..."
            creator = (t.creator_name or "-").replace("|", "\\|")
            created = (t.created_at.strftime("%Y-%m-%d %H:%M") if t.created_at else "-")
            round_label = t.discussion_round.value if hasattr(t.discussion_round, "value") else str(t.discussion_round)
            lines.append(
                f"| {i} | {title} | {round_label} | {t.comment_count} | "
                f"{t.experiment_count} | {creator} | {created} |"
            )
    else:
        lines.append("_(none)_")
    lines.append("")

    # --- Experiments by phase ---
    active_phases = {
        ExperimentPhase.draft,
        ExperimentPhase.review,
        ExperimentPhase.approved,
        ExperimentPhase.running,
        ExperimentPhase.result_review,
    }
    active_exps = [e for e in experiments if e.phase in active_phases]
    done_exps = [e for e in experiments if e.phase == ExperimentPhase.done]
    cancelled_exps = [e for e in experiments if e.phase == ExperimentPhase.cancelled]

    lines.append(f"## Experiments ({len(active_exps)} active / {len(done_exps)} done / {len(cancelled_exps)} cancelled)")
    if active_exps:
        lines.append("| # | Title | Phase | Plan v | Topic | Updated |")
        lines.append("|---|-------|-------|--------|-------|---------|")
        for i, e in enumerate(active_exps, 1):
            title = (e.title or "").replace("|", "\\|")
            if len(title) > 50:
                title = title[:47] + "..."
            updated = (e.updated_at.strftime("%Y-%m-%d %H:%M") if e.updated_at else "-")
            topic = str(e.topic_id)[:8] + "…" if e.topic_id else "-"
            phase_label = e.phase.value if hasattr(e.phase, "value") else str(e.phase)
            lines.append(
                f"| {i} | {title} | {phase_label} | v{e.current_plan_version} | "
                f"{topic} | {updated} |"
            )
    else:
        lines.append("_(no active experiments)_")
    lines.append("")

    # --- Todos summary ---
    todo_buckets = [
        ("pending_reviews", "Pending Reviews", todos.pending_reviews),
        ("pending_result_reviews", "Result Reviews", todos.pending_result_reviews),
        ("pending_topic_replies", "Topic Replies", todos.pending_topic_replies),
        ("pending_round_acks", "Round Acks", todos.pending_round_acks),
        ("pending_advance_rounds", "Advance Rounds", todos.pending_advance_rounds),
        ("stale_open_topics", "Stale Topics", todos.stale_open_topics),
        ("mentions", "Mentions", todos.mentions),
        ("action_items", "Action Items", todos.action_items),
    ]
    obligation_count = sum(len(items) for _, _, items in todo_buckets)
    lines.append(f"## Todos ({obligation_count} obligation items)")
    for _label, display, items in todo_buckets:
        if items:
            lines.append(f"- **{display}**: {len(items)}")
    if obligation_count == 0:
        lines.append("_(no pending obligations)_")
    lines.append("")

    # --- Contextual lists ---
    if todos.my_open_topics:
        lines.append(f"### My Open Topics ({len(todos.my_open_topics)})")
        for t in todos.my_open_topics:
            title = (t.title or "").replace("|", "\\|")
            if len(title) > 60:
                title = title[:57] + "..."
            round_label = t.discussion_round.value if hasattr(t.discussion_round, "value") else str(t.discussion_round)
            lines.append(f"- `{t.id}` — {title} ({round_label}, {t.comment_count} comments)")
        lines.append("")
    if todos.my_open_experiments:
        lines.append(f"### My Open Experiments ({len(todos.my_open_experiments)})")
        for e in todos.my_open_experiments:
            title = (e.title or "").replace("|", "\\|")
            if len(title) > 50:
                title = title[:47] + "..."
            phase_label = e.phase.value if hasattr(e.phase, "value") else str(e.phase)
            lines.append(f"- `{e.id}` — {title} ({phase_label})")
        lines.append("")
    if todos.executor_assignments:
        lines.append(f"### Executor Assignments ({len(todos.executor_assignments)})")
        for e in todos.executor_assignments:
            title = (e.title or "").replace("|", "\\|")
            if len(title) > 50:
                title = title[:47] + "..."
            phase_label = e.phase.value if hasattr(e.phase, "value") else str(e.phase)
            lines.append(f"- `{e.id}` — {title} ({phase_label})")
        lines.append("")

    typer.echo("\n".join(lines))
