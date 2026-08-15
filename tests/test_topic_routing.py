"""Unit tests for PRD v0.11 M51A/B — map topic --id 统一路由层。

Covers:
    - ``_resolve_topic_ref``：uuid → DB 优先 / FS uuid5 反查；slug → FS 优先 /
      DB slug 匹配；--storage fs|db 显式覆盖与非法值。
    - ``map topic comment`` 的 FS 本地优先路由（slug 与 fs-uuid 均写
      round<N>-<persona>.md，全程离线、零 API）。

All tests run in-process via CliRunner (no network, no subprocess).
"""

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from map_client.exceptions import MAPNotFoundError
from map_fs import topic_id_for_slug, write_round_comment, write_topic_index
from typer.testing import CliRunner

from cli.commands.topic import (
    _looks_like_uuid,
    _resolve_topic_ref,
    topic_app,
)

runner = CliRunner()


def _init_workspace(tmp_path: Path, *, personas: dict | None = None) -> None:
    """最小 workspace：.map/config.yaml（+ 可选 agents.yaml persona 反查表）。"""
    map_dir = tmp_path / ".map"
    map_dir.mkdir(parents=True, exist_ok=True)
    (map_dir / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "api_url": "http://localhost:8001",
                "project_key": "test-proj",
                "default_persona": "host",
            }
        ),
        encoding="utf-8",
    )
    if personas is not None:
        (map_dir / "agents.yaml").write_text(
            yaml.safe_dump({"personas": personas}), encoding="utf-8"
        )


@pytest.fixture()
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    _init_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)
    return tmp_path


class _StubClient:
    """_resolve_topic_ref 依赖的最小 client 面（get_topic / list_topics / project 解析）。"""

    def __init__(self, db_topics: list[SimpleNamespace] | None = None) -> None:
        self._topics = db_topics or []
        self.project_id = uuid.uuid4()

    def get_topic(self, topic_id: uuid.UUID):
        for t in self._topics:
            if t.id == topic_id:
                return t
        raise MAPNotFoundError(404, f"topic {topic_id} not found")

    def list_topics(self, project_id: uuid.UUID, **kwargs):
        return self._topics

    def get_project_by_key(self, key: str):
        return SimpleNamespace(id=self.project_id, project_key=key)

    def resolve_project_id(self, project: uuid.UUID | None, project_key: str | None = None):
        return self.project_id


def _db_topic(slug: str) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), slug=slug, title=f"DB {slug}")


def _make_fs_topic(workspace: Path, slug: str) -> None:
    write_topic_index(workspace, slug, title=f"FS {slug}", creator="host")


# ---------------------------------------------------------------------------
# _looks_like_uuid / _resolve_topic_ref（stub client + 真实 workspace 文件夹）
# ---------------------------------------------------------------------------


class TestResolveTopicRef:
    def test_looks_like_uuid(self) -> None:
        assert _looks_like_uuid(str(uuid.uuid4()))
        assert not _looks_like_uuid("fs-demo")
        assert not _looks_like_uuid("not-a-uuid")

    def test_uuid_db_first_then_fs(self, workspace: Path) -> None:
        _make_fs_topic(workspace, "fs-only")
        db = _db_topic("db-one")
        c = _StubClient([db])

        kind, target = _resolve_topic_ref(c, str(db.id), None)
        assert (kind, target) == ("db", db.id)

        # DB miss → FS uuid5 反查命中
        fs_uuid = topic_id_for_slug("fs-only")
        kind, target = _resolve_topic_ref(c, str(fs_uuid), None)
        assert (kind, target) == ("fs", "fs-only")

    def test_slug_fs_first_then_db(self, workspace: Path) -> None:
        _make_fs_topic(workspace, "on-fs")
        db = _db_topic("on-db")
        c = _StubClient([db])

        kind, target = _resolve_topic_ref(c, "on-fs", None)
        assert (kind, target) == ("fs", "on-fs")

        kind, target = _resolve_topic_ref(c, "on-db", None)
        assert (kind, target) == ("db", db.id)

    def test_storage_overrides(self, workspace: Path) -> None:
        # DB slug 与 FS 同名冲突：--storage 显式裁决
        _make_fs_topic(workspace, "clash")
        db = _db_topic("clash")
        c = _StubClient([db])

        assert _resolve_topic_ref(c, "clash", "fs") == ("fs", "clash")
        assert _resolve_topic_ref(c, "clash", "db") == ("db", db.id)
        # uuid + --storage fs 直接走 FS
        assert _resolve_topic_ref(c, str(topic_id_for_slug("clash")), "fs") == ("fs", "clash")

    def test_storage_invalid_errors(self, workspace: Path) -> None:
        c = _StubClient()
        with pytest.raises(Exception) as exc_info:
            _resolve_topic_ref(c, "whatever", "cloud")
        assert exc_info.value.exit_code == 2

    def test_not_found_anywhere(self, workspace: Path) -> None:
        c = _StubClient()
        with pytest.raises(Exception) as slug_err:
            _resolve_topic_ref(c, "ghost", None)
        assert slug_err.value.exit_code == 1
        with pytest.raises(Exception) as uuid_err:
            _resolve_topic_ref(c, str(uuid.uuid4()), None)
        assert uuid_err.value.exit_code == 1

    def test_storage_fs_miss_errors(self, workspace: Path) -> None:
        c = _StubClient([_db_topic("only-db")])
        with pytest.raises(Exception) as exc_info:
            _resolve_topic_ref(c, "only-db", "fs")
        assert exc_info.value.exit_code == 1


# ---------------------------------------------------------------------------
# map topic comment — FS 本地优先路由（CliRunner，全程离线）
# ---------------------------------------------------------------------------


class TestCommentFsRouting:
    def test_comment_by_slug_writes_round_file(self, workspace: Path) -> None:
        _make_fs_topic(workspace, "fs-demo")
        result = runner.invoke(
            topic_app,
            ["comment", "--id", "fs-demo", "--body", "# 观点\n正文"],
        )
        assert result.exit_code == 0, result.output
        target = workspace / "map" / "topics" / "fs-demo" / "round1-host.md"
        assert target.is_file()
        assert "# 观点" in target.read_text(encoding="utf-8")
        assert "fs topic: fs-demo" in result.output

    def test_comment_by_fs_uuid_writes_round_file(self, workspace: Path) -> None:
        _make_fs_topic(workspace, "fs-demo")
        fs_uuid = str(topic_id_for_slug("fs-demo"))
        result = runner.invoke(topic_app, ["comment", "--id", fs_uuid, "--body", "via uuid"])
        assert result.exit_code == 0, result.output
        assert (workspace / "map" / "topics" / "fs-demo" / "round1-host.md").is_file()

    def test_comment_round_summary_flag(self, workspace: Path) -> None:
        _make_fs_topic(workspace, "s")
        result = runner.invoke(
            topic_app,
            ["comment", "--id", "s", "--body", "总结", "--round-summary"],
        )
        assert result.exit_code == 0, result.output
        text = (workspace / "map" / "topics" / "s" / "round1-host.md").read_text(encoding="utf-8")
        assert "round_summary: true" in text

    def test_comment_writes_next_round_after_advance(self, workspace: Path) -> None:
        _make_fs_topic(workspace, "r")
        write_round_comment(workspace, "r", round_number=1, persona="host", body="r1")
        from map_fs import update_topic_index

        update_topic_index(workspace, "r", round="round2")
        result = runner.invoke(topic_app, ["comment", "--id", "r", "--body", "r2 发言"])
        assert result.exit_code == 0, result.output
        assert (workspace / "map" / "topics" / "r" / "round2-host.md").is_file()

    def test_comment_file_path_only_rejected_on_fs(self, workspace: Path) -> None:
        _make_fs_topic(workspace, "fp")
        result = runner.invoke(
            topic_app,
            ["comment", "--id", "fp", "--file-path", "map/x.md", "--excerpt", "e"],
        )
        assert result.exit_code == 2
        assert "fs topics need --body / --file" in result.output

    def test_comment_parent_rejected_on_fs(self, workspace: Path) -> None:
        _make_fs_topic(workspace, "p")
        result = runner.invoke(
            topic_app,
            ["comment", "--id", "p", "--body", "x", "--parent", str(uuid.uuid4())],
        )
        assert result.exit_code == 2
        assert "--parent is a DB-topic option" in result.output

    def test_comment_storage_fs_unknown_topic(self, workspace: Path) -> None:
        result = runner.invoke(topic_app, ["comment", "--id", "ghost", "--storage", "fs", "--body", "x"])
        assert result.exit_code == 1
        assert "fs topic not found" in result.output

    def test_comment_immutable_second_write_errors(self, workspace: Path) -> None:
        _make_fs_topic(workspace, "im")
        first = runner.invoke(topic_app, ["comment", "--id", "im", "--body", "first"])
        assert first.exit_code == 0
        second = runner.invoke(topic_app, ["comment", "--id", "im", "--body", "second"])
        assert second.exit_code == 1
        assert "--force" in second.output or "already exists" in second.output
