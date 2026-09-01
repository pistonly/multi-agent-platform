"""Unit tests for PRD v0.11 M51D — map topic migrate（DB → FS 单向迁移）。

Covers:
    - ``_plan_db_to_fs_migration``：轮次启发式（summary 界定轮次）、
      同人同轮合并、agent_name → persona 反查、decision 落档、
      discussion_round 下限。
    - ``_execute_db_to_fs_migration``：FS 完整落盘后才 archive DB；
      --dry-run 不落盘；目标已存在报错。

All tests run in-process with a stub client（no network）.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from map_fs import scan_plane
from typer.testing import CliRunner

from cli.commands.topic_migrate import _execute_db_to_fs_migration, _plan_db_to_fs_migration

runner = CliRunner()


def _at(minutes: int) -> datetime:
    return datetime(2026, 8, 15, 10, minutes, tzinfo=timezone.utc)


def _comment(
    seq: int,
    *,
    author: str,
    body: str,
    created: datetime,
    summary: bool = False,
    kind: str = "user",
    file_path: str | None = None,
    children: list | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        comment_seq=seq,
        author_name=author,
        body=body,
        excerpt=None,
        is_round_summary=summary,
        kind=kind,
        file_path=file_path,
        created_at=created,
        children=children or [],
    )


def _topic(
    *,
    title: str = "DB Topic",
    status: str = "open",
    round_: str = "round2",
    creator: str = "multi-agent-platform-host",
    comments: list | None = None,
    decision: SimpleNamespace | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        title=title,
        description="背景",
        status=status,
        discussion_round=round_,
        creator_name=creator,
        comments=comments or [],
        decision=decision,
    )


class _MigrateStubClient:
    def __init__(self, topic) -> None:
        self._topic = topic
        self.updated: list[tuple[uuid.UUID, object]] = []

    def get_topic(self, topic_id: uuid.UUID):
        return self._topic

    def update_topic(self, topic_id: uuid.UUID, payload):
        self.updated.append((topic_id, payload))
        return self._topic


@pytest.fixture()
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    map_dir = tmp_path / ".map"
    map_dir.mkdir(parents=True, exist_ok=True)
    (map_dir / "config.yaml").write_text(
        yaml.safe_dump(
            {"api_url": "http://localhost:8001", "project_key": "p", "default_persona": "host"}
        ),
        encoding="utf-8",
    )
    (map_dir / "agents.yaml").write_text(
        yaml.safe_dump(
            {
                "personas": {
                    "host": {"agent_name": "multi-agent-platform-host"},
                    "participant": {"agent_name": "multi-agent-platform-participant"},
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    return tmp_path


class TestPlanDbToFsMigration:
    def test_round_summary_delimits_rounds(self, workspace: Path) -> None:
        topic = _topic(
            comments=[
                _comment(1, author="multi-agent-platform-host", body="开场", created=_at(0)),
                _comment(
                    2,
                    author="multi-agent-platform-participant",
                    body="观点",
                    created=_at(1),
                ),
                _comment(
                    3,
                    author="multi-agent-platform-host",
                    body="轮总结",
                    created=_at(2),
                    summary=True,
                ),
                _comment(4, author="multi-agent-platform-participant", body="第二轮", created=_at(3)),
            ]
        )
        plan = _plan_db_to_fs_migration(topic, workspace)
        files = {(rn, persona): summary for rn, persona, *_rest, summary in plan["files"]}
        assert files == {
            (1, "host"): True,
            (1, "participant"): False,
            (2, "participant"): False,
        }
        assert plan["index"]["round_"] == 2
        assert plan["index"]["status"] == "open"
        assert plan["index"]["creator"] == "host"

    def test_same_person_same_round_merged(self, workspace: Path) -> None:
        topic = _topic(
            comments=[
                _comment(1, author="multi-agent-platform-participant", body="A", created=_at(0)),
                _comment(2, author="multi-agent-platform-participant", body="B", created=_at(1)),
            ]
        )
        plan = _plan_db_to_fs_migration(topic, workspace)
        assert len(plan["files"]) == 1
        rn, persona, body, kind, summary = plan["files"][0]
        assert (rn, persona, kind, summary) == (1, "participant", "user", False)
        assert "A" in body and "B" in body
        assert "---" in body

    def test_file_path_comment_kept_as_reference(self, workspace: Path) -> None:
        topic = _topic(
            comments=[
                _comment(
                    1,
                    author="multi-agent-platform-host",
                    body="",
                    created=_at(0),
                    file_path="map/topics/x/round1-host.md",
                ),
            ]
        )
        plan = _plan_db_to_fs_migration(topic, workspace)
        body = plan["files"][0][2]
        assert "map/topics/x/round1-host.md" in body

    def test_decision_appended_as_yaml_block(self, workspace: Path) -> None:
        decision = SimpleNamespace(
            model_dump=lambda mode="json": {"decision": "adopt", "reason": "收敛"}
        )
        topic = _topic(comments=[], decision=decision)
        plan = _plan_db_to_fs_migration(topic, workspace)
        assert len(plan["files"]) == 1
        rn, persona, body, _kind, _summary = plan["files"][0]
        assert (rn, persona) == (1, "host")
        assert "## Decision" in body
        assert "adopt" in body

    def test_closed_status_and_unknown_agent(self, workspace: Path) -> None:
        topic = _topic(
            status="closed",
            round_="round1",
            comments=[_comment(1, author="external-bot", body="hi", created=_at(0))],
        )
        plan = _plan_db_to_fs_migration(topic, workspace)
        assert plan["index"]["status"] == "closed"
        assert (1, "external-bot") in [(rn, p) for rn, p, *_ in plan["files"]]
        assert "external-bot" in plan["index"]["participants"]


class TestExecuteMigration:
    def _comment_set(self) -> list:
        return [
            _comment(1, author="multi-agent-platform-host", body="开场", created=_at(0)),
            _comment(2, author="multi-agent-platform-participant", body="观点", created=_at(1)),
            _comment(
                3,
                author="multi-agent-platform-host",
                body="总结",
                created=_at(2),
                summary=True,
            ),
        ]

    def test_full_write_then_archive(self, workspace: Path, capsys) -> None:
        topic = _topic(comments=self._comment_set())
        c = _MigrateStubClient(topic)
        tid = uuid.uuid4()

        _execute_db_to_fs_migration(c, tid, "migrated")

        topic_dir = workspace / "map" / "topics" / "migrated"
        assert (topic_dir / "index.md").is_file()
        assert (topic_dir / "round1-host.md").is_file()
        assert (topic_dir / "round1-participant.md").is_file()
        # 只有一个 round 文件组（summary 后无新评论 → 无 round2 文件）
        parsed = scan_plane(workspace).topic_by_slug("migrated")
        assert parsed is not None
        assert len(parsed.comments) == 2
        assert c.updated and c.updated[0][1].archived is True
        out = capsys.readouterr().out
        assert "Archived DB topic" in out

    def test_dry_run_writes_nothing(self, workspace: Path, capsys) -> None:
        topic = _topic(comments=self._comment_set())
        c = _MigrateStubClient(topic)
        _execute_db_to_fs_migration(c, uuid.uuid4(), "ghost", dry_run=True)
        assert not (workspace / "map" / "topics" / "ghost").exists()
        assert c.updated == []
        assert "[dry-run]" in capsys.readouterr().out

    def test_existing_target_dir_errors(self, workspace: Path) -> None:
        (workspace / "map" / "topics" / "exists").mkdir(parents=True)
        topic = _topic()
        c = _MigrateStubClient(topic)
        with pytest.raises(Exception) as exc_info:
            _execute_db_to_fs_migration(c, uuid.uuid4(), "exists")
        assert exc_info.value.exit_code == 1
        assert c.updated == []
