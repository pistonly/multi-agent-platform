from __future__ import annotations

import subprocess
import tempfile
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

    def mention_dismiss(self, mention_id: str) -> dict[str, Any] | None:
        return self._run(["mention", "dismiss", "--id", mention_id])

    def mention_dismiss_all(self) -> dict[str, Any] | None:
        return self._run(["mention", "dismiss-all"])

    def topic_show(self, topic_id: str) -> dict[str, Any]:
        return self._run(["topic", "show", "--id", topic_id])

    def topic_list_open(self) -> list[dict[str, Any]]:
        rows = self._run(["topic", "list", "--status", "open"])
        return list(rows or []) if isinstance(rows, list) else []

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
        return self._run(["experiment", "status", "--id", experiment_id])

    def experiment_submit_review(self, experiment_id: str) -> dict[str, Any] | None:
        return self._run(["experiment", "submit-review", "--id", experiment_id])

    def experiment_reviews_list(self, experiment_id: str) -> list[dict[str, Any]]:
        data = self._run(["experiment", "review", "list", "--id", experiment_id])
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
            ]
        )

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


def _is_write_command(args: list[str]) -> bool:
    if not args:
        return False
    if args[:2] in (
        ["topic", "comment"],
        ["topic", "create"],
        ["topic", "close"],
        ["topic", "reopen"],
        ["topic", "advance-round"],
        ["topic", "resolve"],
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
    if args[:3] == ["experiment", "review", "resolve-item"]:
        return True
    if args[:3] == ["experiment", "lock", "acquire"]:
        return True
    if args[:3] == ["experiment", "lock", "release"]:
        return True
    if args[:3] == ["experiment", "lock", "force-release"]:
        return True
    if args[:3] == ["experiment", "lock", "skip"]:
        return True
    return False
