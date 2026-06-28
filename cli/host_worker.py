from __future__ import annotations

import re
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import typer
import yaml

DEFAULT_REPLY_TEMPLATE = """已收到这个 thread 的反馈。

- 评论摘要：{excerpt}
- 处理状态：已纳入主持跟进；如需进入实验，我会在本话题完成必要讨论后从 host persona 创建关联实验。
"""

ROUND_SUMMARY_RE = re.compile(r"\bround\s+\d+\s+summary\b", re.IGNORECASE)
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
    promote_ready_topics: bool = False
    submit_created_experiment_for_review: bool = False
    min_participant_comments: int = 1
    min_round_summaries: int = 2
    plan_dir: Path | None = None


@dataclass
class WorkerStats:
    cycles: int = 0
    replies_created: int = 0
    experiments_created: int = 0
    dry_run_actions: int = 0


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

    def run_forever(self) -> WorkerStats:
        total = WorkerStats()
        while True:
            stats = self.run_once()
            total.cycles += stats.cycles
            total.replies_created += stats.replies_created
            total.experiments_created += stats.experiments_created
            total.dry_run_actions += stats.dry_run_actions

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

        for item in pending:
            body = self._build_reply(item)
            topic_id = str(item["topic_id"])
            parent_id = str(item.get("comment_id")) if item.get("comment_id") else None
            if self.config.dry_run:
                typer.echo(f"[dry-run] would reply topic={topic_id} parent={parent_id}\n{body}")
                stats.dry_run_actions += 1
            else:
                self.client.topic_comment(topic_id, body, parent_id=parent_id)
                stats.replies_created += 1

        if self.config.promote_ready_topics:
            promoted = self._promote_ready_topics(todos, pending)
            if self.config.dry_run:
                stats.dry_run_actions += promoted
            else:
                stats.experiments_created += promoted

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

    def _promote_ready_topics(self, todos: dict[str, Any], pending: list[dict[str, Any]]) -> int:
        pending_topic_ids = {str(item["topic_id"]) for item in pending if item.get("topic_id")}
        count = 0
        for topic_summary in todos.get("my_open_topics") or []:
            topic_id = str(topic_summary["id"])
            if topic_id in pending_topic_ids:
                continue
            topic = self.client.topic_show(topic_id)
            if not self._topic_ready_for_experiment(topic):
                continue
            self._create_experiment_from_topic(topic)
            count += 1
        return count

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
            if str(c.get("author_agent_id")) == self.host_id and ROUND_SUMMARY_RE.search(c.get("body") or "")
        ]
        return len(round_summaries) >= self.config.min_round_summaries

    def _create_experiment_from_topic(self, topic: dict[str, Any]) -> None:
        topic_id = str(topic["id"])
        title = f"实验：{topic.get('title') or topic_id}"
        plan_content = _default_plan(topic)
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


def _is_write_command(args: list[str]) -> bool:
    if not args:
        return False
    if args[:2] in (
        ["topic", "comment"],
        ["topic", "create"],
        ["topic", "close"],
        ["topic", "reopen"],
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
    return False


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
        help="Create experiments for open topics that satisfy the readiness gate.",
    ),
    submit_for_review: bool = typer.Option(
        False,
        "--submit-for-review",
        help="Submit newly created experiments for review.",
    ),
    min_participant_comments: int = typer.Option(1, "--min-participant-comments", min=1),
    min_round_summaries: int = typer.Option(2, "--min-round-summaries", min=0),
    plan_dir: Path | None = typer.Option(None, "--plan-dir", help="Persist generated plan files in this directory."),
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
        submit_created_experiment_for_review=submit_for_review,
        min_participant_comments=min_participant_comments,
        min_round_summaries=min_round_summaries,
        plan_dir=plan_dir,
    )
    client = MapCommandClient(map_cmd=map_cmd, persona=persona, project_root=project_root, dry_run=dry_run)
    stats = HostWorker(client, config).run_forever()
    typer.echo(yaml.safe_dump(stats.__dict__, allow_unicode=True, sort_keys=False))


def main() -> None:
    typer.run(run)


if __name__ == "__main__":
    main()
