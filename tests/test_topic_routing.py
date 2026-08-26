"""Unit tests for PRD v0.11 M51A/B + v0.12 M56 — map topic --id 统一路由层。

Covers:
    - ``_resolve_topic_ref``：uuid → DB 优先 / FS uuid5 反查；slug → FS 优先 /
      DB slug 匹配；--storage fs|db 显式覆盖与非法值。
    - ``map topic comment`` 的 FS 本地优先路由（slug 与 fs-uuid 均写
      round<N>-<persona>.md，全程离线、零 API）。
    - M56：resolve / rollback-round / reopen / dismiss / read / mark-seen 六命令
      接入三态路由——fs 目标降级（通知投影类 no-op exit 0 / 状态变迁类
      exit 2 + 可执行提示）、DB 分支 SDK 调用参数、--id help 文本分组一致，
      以及 ``experiment cancel`` CLI 封装（成功 + 状态机拒绝透传）。

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
    _filter_local_summaries,
    _fs_topic_to_detail,
    _looks_like_uuid,
    _merge_topic_summaries,
    _resolve_topic_ref,
    _scan_local_fs_summaries,
    _slice_page,
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
        assert "Wrote" in result.output

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
        assert "need --body / --file" in result.output

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
        assert "topic not found" in result.output

    def test_comment_immutable_second_write_errors(self, workspace: Path) -> None:
        _make_fs_topic(workspace, "im")
        first = runner.invoke(topic_app, ["comment", "--id", "im", "--body", "first"])
        assert first.exit_code == 0
        second = runner.invoke(topic_app, ["comment", "--id", "im", "--body", "second"])
        assert second.exit_code == 1
        assert "--force" in second.output or "already exists" in second.output


# ---------------------------------------------------------------------------
# M56：六命令接入三态路由 — fs 目标降级 / DB 分支 / help 一致性 / cancel 封装
# ---------------------------------------------------------------------------

_TRI_STATE_COMMANDS = {
    "show",
    "history",
    "resolve",
    "advance-round",
    "rollback-round",
    "comment",
    "close",
    "reopen",
    "dismiss",
    "read",
    "mark-seen",
}
_DB_ONLY_COMMANDS = {"migrate"}


class _RecordingStub(_StubClient):
    """六命令 DB 分支的最小 client 面：记录 SDK 调用参数。

    返回值用纯字符串 dict（_run 的 yaml 渲染不认 SimpleNamespace / 裸 UUID）。
    """

    def __init__(self, db_topics: list[SimpleNamespace] | None = None) -> None:
        super().__init__(db_topics)
        self.calls: list[tuple[str, uuid.UUID]] = []

    def resolve_topic(self, topic_id, payload):
        self.calls.append(("resolve_topic", topic_id))
        return {"id": str(topic_id), "status": "resolved"}

    def rollback_topic_round(self, topic_id):
        self.calls.append(("rollback_topic_round", topic_id))
        return {"id": str(topic_id), "discussion_round": "round1"}

    def reopen_topic(self, topic_id):
        self.calls.append(("reopen_topic", topic_id))
        return {"id": str(topic_id), "status": "open"}

    def dismiss_topic(self, topic_id):
        self.calls.append(("dismiss_topic", topic_id))
        return {"id": str(topic_id)}

    def mark_topic_read(self, topic_id):
        self.calls.append(("mark_topic_read", topic_id))
        return {"id": str(topic_id)}

    def cancel_experiment(self, experiment_id):
        self.calls.append(("cancel_experiment", experiment_id))
        return {"id": str(experiment_id), "phase": "cancelled"}

    def get_experiment(self, experiment_id):
        """远端 M56D 重构后 _run_lifecycle 的 preflight / after 刷新调用面。

        cancel 命令在调 cancel_experiment 前先 get_experiment(rid) 取
        from_phase（preflight_index），调完因返回 dict 无 phase 属性再取
        一次 after。返回带 phase/plan_file_path/executor_agent_id 的最小
        实验 shape，两条路径都能走通。
        """
        self.calls.append(("get_experiment", experiment_id))
        return SimpleNamespace(
            id=experiment_id,
            title="stub experiment",
            phase="running",
            plan_file_path=None,
            executor_agent_id=None,
            creator_agent_id=uuid.uuid4(),
        )

    def get_me(self):
        """executor persona 推断（after.executor_agent_id is None → host）。"""
        return SimpleNamespace(id=uuid.uuid4(), persona="host")


def _patch_client(monkeypatch: pytest.MonkeyPatch, client: _RecordingStub) -> None:
    import cli.main as cli_main

    def _fake_ctx():
        class _CM:
            def __enter__(self_inner):
                return client

            def __exit__(self_inner, *args):
                return False

        return _CM()

    monkeypatch.setattr(cli_main, "_client_ctx", _fake_ctx)


class TestSixCommandFsDegradation:
    """slug 直接命中 fs 话题：通知投影类 no-op（exit 0）/ 状态变迁类拒绝（exit 2）。

    _run 在进 action 前会构建 client，故测试也挂 stub；stub 的 calls 恒为空
    反证 fs 路由纯本地（slug 命中 map/topics/ 后零 API 调用）。
    """

    @pytest.fixture()
    def api_stub(self, workspace: Path, monkeypatch: pytest.MonkeyPatch) -> _RecordingStub:
        client = _RecordingStub()
        _patch_client(monkeypatch, client)
        return client

    def test_dismiss_fs_noop(self, workspace: Path, api_stub: _RecordingStub) -> None:
        _make_fs_topic(workspace, "fs-dismiss")
        result = runner.invoke(topic_app, ["dismiss", "--id", "fs-dismiss"])
        assert result.exit_code == 0, result.output
        assert "No-op" in result.output
        assert "fs-dismiss" in result.output
        assert "round files" in result.output
        assert api_stub.calls == []

    def test_read_fs_noop(self, workspace: Path, api_stub: _RecordingStub) -> None:
        _make_fs_topic(workspace, "fs-read")
        result = runner.invoke(topic_app, ["read", "--id", "fs-read"])
        assert result.exit_code == 0, result.output
        assert "No-op" in result.output
        assert api_stub.calls == []

    def test_mark_seen_fs_noop(self, workspace: Path, api_stub: _RecordingStub) -> None:
        _make_fs_topic(workspace, "fs-seen")
        result = runner.invoke(topic_app, ["mark-seen", "--id", "fs-seen"])
        assert result.exit_code == 0, result.output
        assert "No-op" in result.output
        assert api_stub.calls == []

    def test_resolve_fs_rejected_with_close_hint(
        self, workspace: Path, api_stub: _RecordingStub
    ) -> None:
        _make_fs_topic(workspace, "fs-resolve")
        resolve_file = workspace / "decision.md"
        resolve_file.write_text("decision text", encoding="utf-8")
        result = runner.invoke(
            topic_app, ["resolve", "--id", "fs-resolve", "--file", str(resolve_file)]
        )
        assert result.exit_code == 2, result.output
        assert "targets DB topics only" in result.output
        assert "map topic close" in result.output
        assert "close_reason" in result.output
        assert api_stub.calls == []

    def test_rollback_round_fs_rejected(self, workspace: Path, api_stub: _RecordingStub) -> None:
        _make_fs_topic(workspace, "fs-roll")
        result = runner.invoke(topic_app, ["rollback-round", "--id", "fs-roll"])
        assert result.exit_code == 2, result.output
        assert "targets DB topics only" in result.output
        assert "round<N>-<persona>.md" in result.output
        assert api_stub.calls == []

    def test_reopen_fs_rejected_with_index_hint(
        self, workspace: Path, api_stub: _RecordingStub
    ) -> None:
        _make_fs_topic(workspace, "fs-reopen")
        result = runner.invoke(topic_app, ["reopen", "--id", "fs-reopen"])
        assert result.exit_code == 2, result.output
        assert "targets DB topics only" in result.output
        assert "index.md" in result.output
        assert api_stub.calls == []

    def test_storage_fs_forces_degradation_even_for_db_slug(
        self, workspace: Path, api_stub: _RecordingStub
    ) -> None:
        # --storage fs 显式覆盖：即使 slug 在 DB 也存在，也按 fs 处理（fs 分支 no-op）
        _make_fs_topic(workspace, "clash2")
        result = runner.invoke(topic_app, ["dismiss", "--id", "clash2", "--storage", "fs"])
        assert result.exit_code == 0, result.output
        assert "No-op" in result.output
        assert api_stub.calls == []


class TestSixCommandDbBranch:
    """DB uuid 路由到 DB 分支。v0.13 M58 退役 DB 写分支：resolve / rollback-round /
    reopen 一律引导性拒绝（exit 2，不触达 SDK）；dismiss / read / mark-seen 为
    通知投影命令保留 DB 路由。"""

    @pytest.fixture()
    def db_env(self, workspace: Path, monkeypatch: pytest.MonkeyPatch):
        db = _db_topic("db-target")
        client = _RecordingStub([db])
        _patch_client(monkeypatch, client)
        return db, client

    def test_resolve_db_uuid_guidance_rejection(self, db_env, workspace: Path) -> None:
        db, client = db_env
        resolve_file = workspace / "decision.md"
        resolve_file.write_text("decision text", encoding="utf-8")
        result = runner.invoke(
            topic_app, ["resolve", "--id", str(db.id), "--file", str(resolve_file)]
        )
        assert result.exit_code == 2, result.output
        assert "DB write path retired" in result.output
        # The hint quotes the full command with args — assert the bare
        # command prefix, not a backtick-closed token.
        assert "map topic close --topic" in result.output
        assert "topic migrate" in result.output
        assert client.calls == []

    def test_rollback_round_db_uuid_guidance_rejection(self, db_env) -> None:
        db, client = db_env
        result = runner.invoke(topic_app, ["rollback-round", "--id", str(db.id)])
        assert result.exit_code == 2, result.output
        assert "DB write path retired" in result.output
        assert "round<N>-*.md" in result.output
        assert "index.md" in result.output
        assert client.calls == []

    def test_reopen_db_uuid_guidance_rejection(self, db_env) -> None:
        db, client = db_env
        result = runner.invoke(topic_app, ["reopen", "--id", str(db.id)])
        assert result.exit_code == 2, result.output
        assert "DB write path retired" in result.output
        assert "index.md" in result.output
        assert client.calls == []

    def test_dismiss_db_uuid(self, db_env) -> None:
        db, client = db_env
        result = runner.invoke(topic_app, ["dismiss", "--id", str(db.id)])
        assert result.exit_code == 0, result.output
        assert client.calls == [("dismiss_topic", db.id)]

    def test_read_and_mark_seen_share_sdk_call(self, db_env) -> None:
        db, client = db_env
        r1 = runner.invoke(topic_app, ["read", "--id", str(db.id)])
        assert r1.exit_code == 0, r1.output
        r2 = runner.invoke(topic_app, ["mark-seen", "--id", str(db.id)])
        assert r2.exit_code == 0, r2.output
        assert client.calls == [
            ("mark_topic_read", db.id),
            ("mark_topic_read", db.id),
        ]

    def test_db_slug_routes_to_db_branch(self, db_env, workspace: Path) -> None:
        # workspace 里没有同名 fs 文件夹 → slug 落到 DB slug 匹配
        db, client = db_env
        result = runner.invoke(topic_app, ["dismiss", "--id", "db-target"])
        assert result.exit_code == 0, result.output
        assert client.calls == [("dismiss_topic", db.id)]


class TestIdHelpConsistency:
    """M56C：--id help 文本分组一致（三态组 10 命令同文案；DB-only 组带标注）。"""

    def test_tri_state_id_help_identical(self) -> None:
        expected = "Topic UUID (DB), folder uuid5 id, or slug."
        for name in sorted(_TRI_STATE_COMMANDS):
            result = runner.invoke(topic_app, [name, "--help"])
            assert result.exit_code == 0, name
            assert expected in result.output, f"{name}: --id help 文案漂移"

    def test_db_only_id_help_annotated(self) -> None:
        for name in sorted(_DB_ONLY_COMMANDS):
            result = runner.invoke(topic_app, [name, "--help"])
            assert result.exit_code == 0, name
            assert "DB" in result.output, f"{name}: DB-only 标注缺失"

    def test_topic_command_groups_are_closed(self) -> None:
        # 无 --id 的命令固定为 create/list/progress；其余必须在两个分组内，
        # 防止未来新增命令悄悄游离在分组断言之外
        from cli.commands.topic import topic_app

        no_id = {
            "create",
            "list",
            "progress",
            "init",
            "anomalies",
            "work",
            "archive-index",
            "migrate-from-docs",
            "archive",
        }
        for info in topic_app.registered_commands:
            if info.name in no_id:
                continue
            assert info.name in _TRI_STATE_COMMANDS | _DB_ONLY_COMMANDS, info.name


class TestExperimentCancelCli:
    """M56D：experiment cancel 封装（成功 + 状态机拒绝透传）。"""

    def test_cancel_success(self, workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from cli.commands.experiment import experiment_app

        exp_id = uuid.uuid4()
        client = _RecordingStub()
        _patch_client(monkeypatch, client)
        result = runner.invoke(experiment_app, ["cancel", "--id", str(exp_id)])
        assert result.exit_code == 0, result.output
        # M56D 重构后 lifecycle 命令先 get_experiment 取 from_phase、
        # 调 API 后再 get_experiment 刷新 after（cancel 返回 dict 无 phase）。
        assert client.calls == [
            ("get_experiment", exp_id),
            ("cancel_experiment", exp_id),
            ("get_experiment", exp_id),
        ]
        assert "cancelled" in result.output

    def test_cancel_state_machine_rejection_passthrough(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from map_client.exceptions import MAPHTTPError

        from cli.commands.experiment import experiment_app

        class _Rejecting(_RecordingStub):
            def cancel_experiment(self, experiment_id):
                raise MAPHTTPError(
                    422,
                    "Experiment is not in a cancellable phase",
                    error_code="STATE_MACHINE_EXPERIMENT_CANCEL_MISUSE",
                    hint="cancel is only allowed in running/review phases",
                )

        client = _Rejecting()
        _patch_client(monkeypatch, client)
        result = runner.invoke(experiment_app, ["cancel", "--id", str(uuid.uuid4())])
        assert result.exit_code == 1, result.output
        assert "STATE_MACHINE_EXPERIMENT_CANCEL_MISUSE" in result.output
        assert "cancel is only allowed" in result.output


# ---------------------------------------------------------------------------
# map topic 统一入口：create 转发 FS；list 合并本地 map/
# ---------------------------------------------------------------------------


class TestTopicCreateFsForward:
    def test_create_writes_index(self, workspace: Path) -> None:
        result = runner.invoke(
            topic_app,
            [
                "create",
                "--title",
                "Hello World",
                "--slug",
                "hello-world",
                "--participants",
                "host,participant",
            ],
        )
        assert result.exit_code == 0, result.output
        index = workspace / "map" / "topics" / "hello-world" / "index.md"
        assert index.is_file()
        assert "Created" in result.output
        text = index.read_text(encoding="utf-8")
        assert "Hello World" in text
        assert "participant" in text

    def test_create_slugifies_title_when_slug_omitted(self, workspace: Path) -> None:
        result = runner.invoke(topic_app, ["create", "--title", "Discuss API"])
        assert result.exit_code == 0, result.output
        assert (workspace / "map" / "topics" / "discuss-api" / "index.md").is_file()


class TestCreateMissingOptionGuards:
    """回归：typer 0.16.1 + click 8.4.x 不强制校验必填选项，缺失参数以 None
    穿透到函数体（``fs topic-create --title X`` 缺 --slug 曾直接抛
    ``TypeError: PosixPath / NoneType``）。两个 create 入口必须自行兜底：
    slug 缺省由 title 生成、title 缺省明确报错，不能崩。"""

    def test_topic_create_without_slug_slugifies_title(self, workspace: Path) -> None:
        result = runner.invoke(topic_app, ["create", "--title", "Auto Slug Topic"])
        assert result.exit_code == 0, result.output
        assert (workspace / "map" / "topics" / "auto-slug-topic" / "index.md").is_file()

    def test_topic_create_without_title_errors_cleanly(self, workspace: Path) -> None:
        result = runner.invoke(topic_app, ["create"])
        # click 恢复必填校验的版本下是 exit 2（usage error），两种都算已修复
        assert result.exit_code in (1, 2), result.output
        assert "--title" in result.output


class TestTopicListLocalMerge:
    def test_scan_local_fs_summaries(self, workspace: Path) -> None:
        _make_fs_topic(workspace, "local-only")
        pid = uuid.uuid4()
        summaries = _scan_local_fs_summaries(pid)
        assert [t.slug for t in summaries] == ["local-only"]
        assert summaries[0].project_id == pid
        assert summaries[0].title == "FS local-only"
        assert summaries[0].creator_name == "host"

    def test_merge_keeps_api_on_slug_overlap(self, workspace: Path) -> None:
        pid = uuid.uuid4()
        _make_fs_topic(workspace, "shared")
        local = _scan_local_fs_summaries(pid)
        api = [local[0].model_copy(update={"title": "from-api"})]
        merged = _merge_topic_summaries(local, api)
        assert len(merged) == 1
        assert merged[0].title == "from-api"

    def test_merge_prepends_local_only_slug(self, workspace: Path) -> None:
        pid = uuid.uuid4()
        _make_fs_topic(workspace, "a")
        _make_fs_topic(workspace, "b")
        by_slug = {t.slug: t for t in _scan_local_fs_summaries(pid)}
        merged = _merge_topic_summaries([by_slug["a"]], [by_slug["b"]])
        assert [t.slug for t in merged] == ["a", "b"]

    def test_filter_status_and_creator(self, workspace: Path) -> None:
        write_topic_index(workspace, "open-one", title="Open", creator="host")
        write_topic_index(workspace, "closed-one", title="Closed", creator="host", status="closed")
        pid = uuid.uuid4()
        scanned = _scan_local_fs_summaries(pid)
        opened = _filter_local_summaries(
            scanned, status="open", creator=None, creator_agent_id=None, q=None
        )
        assert {t.slug for t in opened} == {"open-one"}
        by_creator = _filter_local_summaries(
            scanned, status=None, creator="host", creator_agent_id=None, q=None
        )
        assert {t.slug for t in by_creator} == {"open-one", "closed-one"}
        other = _filter_local_summaries(
            scanned, status=None, creator="participant", creator_agent_id=None, q=None
        )
        assert other == []

    def test_slice_page(self) -> None:
        assert _slice_page(list(range(5)), 2, 2) == [2, 3]

    def test_fs_topic_to_detail(self, workspace: Path) -> None:
        from map_fs import parse_topic_dir

        _make_fs_topic(workspace, "d")
        parsed = parse_topic_dir(workspace / "map" / "topics" / "d", workspace)
        assert parsed is not None
        detail = _fs_topic_to_detail(parsed)
        assert detail.slug == "d"
        assert detail.title == "FS d"
        assert detail.creator == "host"
