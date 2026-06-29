import subprocess
from pathlib import Path

import pytest

from cli.git_checkpoint import GitCheckpointError, checkpoint_after, checkpoint_before, head_sha


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def test_checkpoint_before_and_after_in_temp_repo(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("v1\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "init")

    before = checkpoint_before(repo, "exp-1")
    assert before == head_sha(repo)

    (repo / "README.md").write_text("v2\n", encoding="utf-8")
    after = checkpoint_after(repo, "exp-1", "updated readme")
    assert after is not None
    assert after != before
    assert "v2" in (repo / "README.md").read_text(encoding="utf-8")


def test_checkpoint_after_returns_none_when_no_changes(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "commit", "--allow-empty", "-m", "init")
    checkpoint_before(repo, "exp-2")
    assert checkpoint_after(repo, "exp-2", "no changes") is None


def test_checkpoint_fails_outside_git(tmp_path: Path) -> None:
    with pytest.raises(GitCheckpointError):
        checkpoint_before(tmp_path, "exp-3")
