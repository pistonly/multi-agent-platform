"""Git checkpoint helpers for MAP experiment execution (before/after code changes)."""

from __future__ import annotations

import subprocess
from pathlib import Path


class GitCheckpointError(RuntimeError):
    pass


def _run_git(repo: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise GitCheckpointError(f"git {' '.join(args)} failed: {detail}")
    return result


def _has_staged_changes(repo: Path) -> bool:
    result = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=repo,
        text=True,
        check=False,
    )
    return result.returncode != 0


def _has_unstaged_or_staged_changes(repo: Path) -> bool:
    status = _run_git(repo, ["status", "--porcelain"])
    return bool(status.stdout.strip())


def head_sha(repo: Path) -> str:
    return _run_git(repo, ["rev-parse", "HEAD"]).stdout.strip()


def checkpoint_before(repo: Path, experiment_id: str) -> str:
    """Commit current tree (or empty checkpoint) before experiment modifies the repo."""
    if not (repo / ".git").exists():
        raise GitCheckpointError(f"Not a git repository: {repo}")
    _run_git(repo, ["add", "-A"])
    message = f"map: checkpoint before experiment {experiment_id}"
    if _has_staged_changes(repo):
        _run_git(repo, ["commit", "-m", message])
    else:
        _run_git(repo, ["commit", "--allow-empty", "-m", message])
    return head_sha(repo)


def checkpoint_after(repo: Path, experiment_id: str, summary: str) -> str | None:
    """Commit experiment changes after execution. Returns SHA or None if nothing to commit."""
    if not (repo / ".git").exists():
        raise GitCheckpointError(f"Not a git repository: {repo}")
    _run_git(repo, ["add", "-A"])
    if not _has_staged_changes(repo):
        return None
    short = summary.strip().replace("\n", " ")[:120]
    _run_git(repo, ["commit", "-m", f"map: experiment {experiment_id} — {short}"])
    return head_sha(repo)
