"""M1: experiment index.md 契约 + 验证型写（非法手改 phase 拒绝）。"""
from __future__ import annotations

from pathlib import Path

import pytest
from map_fs import (
    ExperimentIndexError,
    commit_experiment_index_write,
    experiment_id_for_slug,
    parse_experiment_dir,
    scan_plane,
    update_experiment_index,
    validate_experiment_index_file,
    write_experiment_index,
    write_experiment_review_yaml,
)

from cli.experiment_fs import slug_from_plan_file_path


def test_experiment_id_for_slug_is_stable() -> None:
    a = experiment_id_for_slug("lifecycle-m1")
    b = experiment_id_for_slug("lifecycle-m1")
    assert a == b
    assert a != experiment_id_for_slug("other")


def test_write_and_parse_index_contract(tmp_path: Path) -> None:
    projection = experiment_id_for_slug("demo-exp")
    path = write_experiment_index(
        tmp_path,
        "demo-exp",
        title="Demo",
        creator="host",
        phase="draft",
        current_plan_version=1,
        executor="",
        topic="source-topic",
        description="body",
        projection_id=projection,
    )
    assert path.is_file()
    parsed = parse_experiment_dir(tmp_path / "map" / "experiments" / "demo-exp", tmp_path)
    assert parsed is not None
    assert parsed.phase == "draft"
    assert parsed.current_plan_version == 1
    assert parsed.creator == "host"
    assert parsed.executor == ""
    assert parsed.topic == "source-topic"
    assert parsed.updated_at is not None
    assert parsed.projection_id == projection
    assert parsed.id == experiment_id_for_slug("demo-exp")
    validate_experiment_index_file(tmp_path, "demo-exp")


def test_hand_edited_phase_rejected_on_commit(tmp_path: Path) -> None:
    write_experiment_index(
        tmp_path,
        "tamper",
        title="Tamper",
        creator="host",
        phase="running",
        current_plan_version=1,
        executor="host",
        topic="t",
    )
    index = tmp_path / "map" / "experiments" / "tamper" / "index.md"
    text = index.read_text(encoding="utf-8")
    index.write_text(text.replace("phase: running", "phase: done"), encoding="utf-8")

    with pytest.raises(ExperimentIndexError, match="hand-edited phase"):
        commit_experiment_index_write(
            tmp_path,
            "tamper",
            expected_from_phase="running",
            target_phase="result_review",
        )
    # 非法目标 phase 未落盘；手改后的 done 仍在（validate 拒绝写回）。
    parsed = parse_experiment_dir(tmp_path / "map" / "experiments" / "tamper", tmp_path)
    assert parsed is not None
    assert parsed.phase == "done"


def test_validate_is_independently_callable(tmp_path: Path) -> None:
    write_experiment_index(
        tmp_path,
        "v-exp",
        title="V",
        creator="host",
        phase="approved",
        executor="host",
        topic="t",
    )
    meta = validate_experiment_index_file(
        tmp_path,
        "v-exp",
        expected_from_phase="approved",
        target_phase="running",
    )
    assert meta["phase"] == "approved"
    with pytest.raises(ExperimentIndexError, match="illegal experiment phase write"):
        validate_experiment_index_file(
            tmp_path,
            "v-exp",
            expected_from_phase="approved",
            target_phase="done",
        )


def test_legal_complete_writeback_updates_phase_and_reviews(tmp_path: Path) -> None:
    write_experiment_index(
        tmp_path,
        "done-exp",
        title="Done path",
        creator="host",
        phase="running",
        current_plan_version=1,
        executor="host",
        topic="t",
    )
    commit_experiment_index_write(
        tmp_path,
        "done-exp",
        expected_from_phase="running",
        target_phase="result_review",
        current_plan_version=1,
    )
    review = write_experiment_review_yaml(
        tmp_path,
        "done-exp",
        "complete.yaml",
        {"event": "complete", "summary": "ok"},
    )
    parsed = parse_experiment_dir(tmp_path / "map" / "experiments" / "done-exp", tmp_path)
    assert parsed is not None
    assert parsed.phase == "result_review"
    assert review.is_file()
    assert "complete.yaml" in review.name
    plane = scan_plane(tmp_path)
    match = [e for e in plane.experiments if e.slug == "done-exp"]
    assert len(match) == 1
    assert match[0].phase == "result_review"


def test_same_phase_plan_version_bump_allowed(tmp_path: Path) -> None:
    write_experiment_index(
        tmp_path,
        "rev",
        title="Rev",
        creator="host",
        phase="running",
        current_plan_version=1,
        executor="host",
        topic="t",
    )
    update_experiment_index(tmp_path, "rev", current_plan_version=2)
    parsed = parse_experiment_dir(tmp_path / "map" / "experiments" / "rev", tmp_path)
    assert parsed is not None
    assert parsed.current_plan_version == 2
    assert parsed.phase == "running"


def test_slug_from_plan_file_path() -> None:
    assert (
        slug_from_plan_file_path("map/experiments/experiment-lifecycle-fs-m1/plan.md")
        == "experiment-lifecycle-fs-m1"
    )
    assert slug_from_plan_file_path(None) is None


def test_lock_untouched_by_index_write(tmp_path: Path) -> None:
    """A5: index.md 写回与 running 锁无关——本函数不创建任何 lock 文件。"""
    write_experiment_index(
        tmp_path,
        "lock-exp",
        title="Lock",
        creator="host",
        phase="approved",
        executor="host",
        topic="t",
    )
    commit_experiment_index_write(
        tmp_path,
        "lock-exp",
        expected_from_phase="approved",
        target_phase="running",
    )
    exp_dir = tmp_path / "map" / "experiments" / "lock-exp"
    names = {p.name for p in exp_dir.iterdir()}
    assert "index.md" in names
    assert "lock" not in names
    assert not any(n.startswith("lock") for n in names)
