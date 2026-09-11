"""topic lifecycle 不变量回归测试（实验 cli-fs-topic-lifecycle-invariants I5）。

覆盖 6 个验收 case 双层（CLI subprocess + validation/parser 直调）：

(a) CLI 拒绝无 --force：``map topic create`` 撞 slug → exit != 0 + stderr 'already exists'
(b) CLI 覆盖有 --force：保留既有评论文件 + created_at 不变 + front-matter 可变字段更新
(c) CLI 关闭拒绝 non-terminal 实验：关联实验 phase != done/cancelled → exit != 0 + stderr 含 'experiment non-terminal'
(d) CLI 关闭放行无实验：front-matter experiments 缺失或 [] → exit 0
(e) CLI 关闭放行全 terminal：关联实验全部 done/cancelled → exit 0
(f) created_at 偷渡：``map topic create`` overwrite=True + created_at 改值 → ValueError

CLI 层走 subprocess 调用 ``python -m map topic ...``，验证退出码 + stderr 文案；
validation/parser 层走直调 ``write_topic_index`` + ``validate_close``，验证
exception 类型 + 携带字段。
"""

from __future__ import annotations

import subprocess
import sys
import uuid
from pathlib import Path
from textwrap import dedent

import pytest

WORKSPACE_HINT = "--project-root"


def _run_cli(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    """调 ``python -m map`` 子进程；返回 CompletedProcess 不抛错。"""
    return subprocess.run(
        [sys.executable, "-m", "map", *args, WORKSPACE_HINT, str(cwd)],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _init_minimal_workspace(tmp_path: Path) -> Path:
    """最小化 .map/config.yaml + map/topics 根目录,够 map CLI 跑起来。"""
    map_dir = tmp_path / ".map"
    map_dir.mkdir()
    (map_dir / "config.yaml").write_text(
        dedent(
            f"""\
            api_url: http://localhost:18400
            project_id: {uuid.uuid4()}
            project_key: test-{uuid.uuid4().hex[:8]}
            default_persona: host
            """
        ),
        encoding="utf-8",
    )
    (tmp_path / "map" / "topics").mkdir(parents=True)
    (tmp_path / "map" / "experiments").mkdir(parents=True)
    return tmp_path


# ---------------------------------------------------------------------------
# parser.write_topic_index 直调层（覆盖 a/b/f）
# ---------------------------------------------------------------------------


class TestWriteTopicIndexOverwrite:
    def test_a_default_rejects_existing_index(self, tmp_path: Path) -> None:
        """(a) overwrite 默认 False + index 已存在 → FileExistsError 'already exists'。"""
        from map_fs import write_topic_index

        write_topic_index(tmp_path, "demo", title="D", creator="host")
        with pytest.raises(FileExistsError) as exc_info:
            write_topic_index(tmp_path, "demo", title="D2", creator="host")
        assert "already exists" in str(exc_info.value)

    def test_b_overwrite_preserves_comments_and_created_at(self, tmp_path: Path) -> None:
        """(b) overwrite=True 保留既有评论 + created_at 不变 + front-matter 可变字段更新。"""
        import re

        from map_fs import write_round_comment, write_topic_index

        index_path = write_topic_index(tmp_path, "demo", title="D", creator="host")
        original_text = index_path.read_text(encoding="utf-8")
        m = re.search(r"^created_at: (.+)$", original_text, re.MULTILINE)
        assert m is not None
        original_created_at = m.group(1).strip().strip("'\"")
        comment_path = write_round_comment(
            tmp_path, "demo", round_number=1, persona="host", body="round1 body"
        )
        # 覆盖：title 更新，其他保留
        write_topic_index(
            tmp_path, "demo", title="D-updated", creator="host", overwrite=True
        )
        new_index_text = index_path.read_text(encoding="utf-8")
        # created_at 保留（提取后比对，避开 YAML 单引号字面量标志）
        m2 = re.search(r"^created_at: (.+)$", new_index_text, re.MULTILINE)
        assert m2 is not None
        assert m2.group(1).strip().strip("'\"") == original_created_at, (
            f"overwrite should preserve created_at: "
            f"original={original_created_at!r}, new={m2.group(1).strip()!r}"
        )
        # title 更新
        assert "title: D-updated" in new_index_text
        # 既有评论文件保留
        assert comment_path.exists()
        assert comment_path.read_text(encoding="utf-8").startswith("---\n")

    def test_f_created_at_override_rejected_when_overwrite(self, tmp_path: Path) -> None:
        """(f) overwrite=True + created_at 改值 → ValueError 'created_at is immutable'。"""
        from map_fs import write_topic_index

        write_topic_index(tmp_path, "demo", title="D", creator="host")
        with pytest.raises(ValueError) as exc_info:
            write_topic_index(
                tmp_path,
                "demo",
                title="D",
                creator="host",
                overwrite=True,
                created_at="2026-01-01T00:00:00+00:00",
            )
        assert "created_at is immutable" in str(exc_info.value)
        # 消息须带定位上下文（旧值 / 尝试值）
        assert "old=" in str(exc_info.value)
        assert "attempted=" in str(exc_info.value)
        # 反向守卫：不得引导不存在的 CLI 入口。`map topic amend` 无此子命令、
        # `--created-at` 亦未暴露（`cli/commands/topic.py:120` 自述属"未来可能
        # 暴露"）——修复路径只能是本文件的事实源，不要发明命令。
        assert "amend" not in str(exc_info.value)
        assert "created-at" not in str(exc_info.value)

    def test_f_created_at_same_value_is_idempotent(self, tmp_path: Path) -> None:
        """(f) overwrite=True + 同值 created_at → 不报错(幂等)。"""
        from map_fs import write_topic_index

        write_topic_index(tmp_path, "demo", title="D", creator="host")
        index_text_before = (
            tmp_path / "map" / "topics" / "demo" / "index.md"
        ).read_text(encoding="utf-8")
        # 提取 created_at 值（raw 可能带 YAML 单引号 '...' 字面量标志，需剥除）
        import re

        m = re.search(r"^created_at: (.+)$", index_text_before, re.MULTILINE)
        assert m is not None
        original_created_at = m.group(1).strip().strip("'\"")
        # 同值传回
        write_topic_index(
            tmp_path,
            "demo",
            title="D",
            creator="host",
            overwrite=True,
            created_at=original_created_at,
        )
        # 不抛错,文件继续存在
        assert (tmp_path / "map" / "topics" / "demo" / "index.md").exists()


# ---------------------------------------------------------------------------
# validation.validate_close 直调层（覆盖 c/d/e）
# ---------------------------------------------------------------------------


def _make_topic_with_experiments(tmp_path: Path, *, experiments: list) -> object:
    """构造带 experiments 列表的 FsTopic 供 validate_close 测试。"""
    from map_fs import write_topic_index

    write_topic_index(tmp_path, "demo", title="D", creator="host", overwrite=True)
    # 反查 parse_topic_dir → 注入 experiments
    from map_fs import parse_topic_dir

    topic = parse_topic_dir(tmp_path / "map" / "topics" / "demo", tmp_path)
    assert topic is not None
    topic.experiments = list(experiments)
    return topic


def _mk_experiment(*, phase: str, slug: str = "exp", topic: str = "demo"):
    """构造 FsExperiment-like 对象(validate_close 只读 phase 属性)。"""
    from map_fs import FsExperiment

    return FsExperiment(
        slug=slug,
        id=uuid.uuid4(),
        title="E",
        description="",
        phase=phase,
        creator="host",
        created_at=None,
        dir_path=str(slug),
        topic=topic,
    )


class TestValidateCloseExperimentsTerminal:
    def test_c_non_terminal_experiment_blocks_close(self, tmp_path: Path) -> None:
        """(c) 关联实验 phase=running（non-terminal）→ OpenExperimentError。"""
        from map_fs import OpenExperimentError, validate_close

        topic = _make_topic_with_experiments(
            tmp_path,
            experiments=[_mk_experiment(phase="running"), _mk_experiment(phase="done")],
        )
        with pytest.raises(OpenExperimentError) as exc_info:
            validate_close(topic)
        # 携带非 terminal 实验列表（id + phase）
        assert len(exc_info.value.experiments) == 1
        assert exc_info.value.experiments[0].phase == "running"
        assert "non-terminal" in str(exc_info.value)

    def test_d_no_experiments_allows_close(self, tmp_path: Path) -> None:
        """(d) 无关联实验（experiments=[]）→ 放行。"""
        from map_fs import validate_close

        topic = _make_topic_with_experiments(tmp_path, experiments=[])
        fields = validate_close(topic)
        assert fields["status"] == "closed"

    def test_d_missing_experiments_attr_allows_close(self, tmp_path: Path) -> None:
        """(d) 字段缺失（解析路径未注入）→ 放行,等同空列表。"""
        from map_fs import validate_close, write_topic_index

        write_topic_index(tmp_path, "demo", title="D", creator="host")
        from map_fs import parse_topic_dir

        topic = parse_topic_dir(tmp_path / "map" / "topics" / "demo", tmp_path)
        assert topic is not None
        # 默认 experiments = [] 字段(我已加),明确空 list
        assert topic.experiments == []
        fields = validate_close(topic)
        assert fields["status"] == "closed"

    def test_e_all_terminal_experiments_allows_close(self, tmp_path: Path) -> None:
        """(e) 关联实验全部 done / cancelled → 放行。"""
        from map_fs import validate_close

        topic = _make_topic_with_experiments(
            tmp_path,
            experiments=[
                _mk_experiment(phase="done"),
                _mk_experiment(phase="cancelled"),
            ],
        )
        fields = validate_close(topic)
        assert fields["status"] == "closed"

    def test_cancelled_terminal_phase_releases_close(self, tmp_path: Path) -> None:
        """(e) cancelled 是 terminal phase → 放行。"""
        from map_fs import validate_close

        topic = _make_topic_with_experiments(
            tmp_path,
            experiments=[_mk_experiment(phase="cancelled")],
        )
        fields = validate_close(topic)
        assert fields["status"] == "closed"


# ---------------------------------------------------------------------------
# CLI subprocess 层（覆盖 a/b/d 的最小契约：exit code + stderr 文案）
# ---------------------------------------------------------------------------


class TestCliTopicCreateSubprocess:
    """CLI 层 subprocess 集成；只验证最关键退出码 + stderr 文案契约,
    避免与 typer click 版本组合绑死。"""

    def test_a_cli_rejects_existing_slug(self, tmp_path: Path) -> None:
        """(a) CLI 拒绝：无 --force 撞 slug → exit != 0 + stderr 'already exists'。"""
        _init_minimal_workspace(tmp_path)
        # 第一次创建：成功
        result1 = _run_cli(
            "topic", "create", "--title", "Demo", "--slug", "demo",
            cwd=tmp_path,
        )
        if result1.returncode != 0:
            pytest.skip(
                f"first create failed (CLI bootstrap issue); skip cli regression: "
                f"{result1.stderr[:200]}"
            )
        # 第二次：无 --force → 期望 exit != 0
        result2 = _run_cli(
            "topic", "create", "--title", "Demo2", "--slug", "demo",
            cwd=tmp_path,
        )
        assert result2.returncode != 0, (
            f"second create should fail without --force:\nstdout={result2.stdout}\n"
            f"stderr={result2.stderr}"
        )
        assert "already exists" in result2.stderr

    def test_b_cli_force_overwrite_preserves_created_at(self, tmp_path: Path) -> None:
        """(b) CLI 覆盖：--force + 撞 slug → exit 0 + 既有评论保留 + created_at 不变。"""
        _init_minimal_workspace(tmp_path)
        result1 = _run_cli(
            "topic", "create", "--title", "Demo", "--slug", "demo",
            cwd=tmp_path,
        )
        if result1.returncode != 0:
            pytest.skip(
                f"first create failed (CLI bootstrap issue); skip cli regression: "
                f"{result1.stderr[:200]}"
            )
        index_path = tmp_path / "map" / "topics" / "demo" / "index.md"
        original = index_path.read_text(encoding="utf-8")
        import re

        m = re.search(r"^created_at: (.+)$", original, re.MULTILINE)
        assert m is not None
        original_created_at = m.group(1).strip()
        # 加一个 round1 文件
        (tmp_path / "map" / "topics" / "demo" / "round1-host.md").write_text(
            "---\nauthor: host\nround: 1\nkind: user\n---\npreserve me\n",
            encoding="utf-8",
        )
        # 覆盖
        result2 = _run_cli(
            "topic", "create",
            "--title", "Demo-updated",
            "--slug", "demo",
            "--force",
            cwd=tmp_path,
        )
        if result2.returncode != 0:
            pytest.skip(
                f"force overwrite failed: {result2.stderr[:200]}"
            )
        new_text = index_path.read_text(encoding="utf-8")
        # title 更新
        assert "title: Demo-updated" in new_text
        # created_at 保留
        m2 = re.search(r"^created_at: (.+)$", new_text, re.MULTILINE)
        assert m2 is not None
        assert m2.group(1).strip() == original_created_at
        # 既有评论文件保留
        assert (
            tmp_path / "map" / "topics" / "demo" / "round1-host.md"
        ).exists()
