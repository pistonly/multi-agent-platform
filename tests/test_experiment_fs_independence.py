"""Experiment data independence: FS-only show/list + sync --check (no live server)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

from map_client.exceptions import MAPNotFoundError
from map_fs import experiment_id_for_slug, write_experiment_index
from map_types.enums import ExperimentPhase
from map_types.schemas import ExperimentSummaryRead

from cli.commands.experiment import _load_experiment
from cli.experiment_fs import (
    experiment_sync_check,
    filter_experiment_summaries,
    fs_experiment_to_detail,
    iter_indexed_experiments,
    lookup_fs_for_ref,
    merge_experiment_summaries,
    overlay_fs_authority,
    persona_agent_id,
)

_PROJECT = uuid.UUID("106216a7-a086-44b4-bb0b-7693b171154a")
_NOW = datetime(2026, 8, 25, 8, 0, tzinfo=timezone.utc)


def _write_index(tmp_path: Path, slug: str, **kwargs) -> None:
    defaults = dict(
        title=slug.replace("-", " "),
        creator="host",
        phase="done",
        current_plan_version=1,
        executor="host",
        topic="",
    )
    defaults.update(kwargs)
    write_experiment_index(tmp_path, slug, **defaults)


def _summary(**kwargs) -> ExperimentSummaryRead:
    payload = dict(
        id=uuid.uuid4(),
        project_id=_PROJECT,
        creator_agent_id=persona_agent_id("host"),
        title="db title",
        description=None,
        phase=ExperimentPhase.running,
        current_plan_version=1,
        created_at=_NOW,
        updated_at=_NOW,
        plan_file_path=None,
    )
    payload.update(kwargs)
    return ExperimentSummaryRead(**payload)


def test_fs_only_detail_does_not_need_db_row(tmp_path: Path) -> None:
    _write_index(tmp_path, "fs-only-exp", phase="done", title="FS Only")
    (tmp_path / "map" / "experiments" / "fs-only-exp" / "plan.md").write_text(
        "# plan\n", encoding="utf-8"
    )
    fs = lookup_fs_for_ref("fs-only-exp", tmp_path)
    assert fs is not None
    detail = fs_experiment_to_detail(fs, _PROJECT, tmp_path)
    assert detail.phase == ExperimentPhase.done
    assert detail.id == experiment_id_for_slug("fs-only-exp")
    assert detail.blocked_on == "no_db_projection"
    assert "fs_only_no_db_projection" in detail.warnings
    assert detail.current_plan is not None
    assert detail.current_plan.content_md.startswith("# plan")
    assert detail.source is not None
    assert detail.source.content_source == "fs"


def test_overlay_prefers_index_phase_and_keeps_db_id(tmp_path: Path) -> None:
    db_id = uuid.UUID("34840a7a-02d0-4def-835d-022396154bf2")
    _write_index(
        tmp_path,
        "matched-exp",
        phase="done",
        title="From FS",
        current_plan_version=2,
        projection_id=db_id,
    )
    db = _summary(
        id=db_id,
        title="From DB",
        phase=ExperimentPhase.running,
        current_plan_version=1,
        plan_file_path="map/experiments/matched-exp/plan.md",
    )
    overlaid = overlay_fs_authority(db, tmp_path)
    assert overlaid.id == db_id
    assert overlaid.phase == ExperimentPhase.done
    assert overlaid.current_plan_version == 2
    assert overlaid.title == "From FS"


def test_merge_includes_fs_only_once(tmp_path: Path) -> None:
    db_id = uuid.uuid4()
    _write_index(tmp_path, "db-matched", phase="done", projection_id=db_id)
    _write_index(tmp_path, "local-only", phase="done")
    api = [
        _summary(
            id=db_id,
            title="matched",
            phase=ExperimentPhase.running,
            plan_file_path="map/experiments/db-matched/plan.md",
        )
    ]
    merged = merge_experiment_summaries(
        iter_indexed_experiments(tmp_path), api, _PROJECT, tmp_path
    )
    ids = {item.id for item in merged}
    assert db_id in ids
    assert experiment_id_for_slug("local-only") in ids
    assert len(merged) == 2
    matched = next(item for item in merged if item.id == db_id)
    assert matched.phase == ExperimentPhase.done


def test_filter_uses_fs_overlaid_phase(tmp_path: Path) -> None:
    db_id = uuid.uuid4()
    _write_index(tmp_path, "now-done", phase="done", projection_id=db_id)
    api = [
        _summary(
            id=db_id,
            phase=ExperimentPhase.running,
            plan_file_path="map/experiments/now-done/plan.md",
        )
    ]
    merged = merge_experiment_summaries(
        iter_indexed_experiments(tmp_path), api, _PROJECT, tmp_path
    )
    done = filter_experiment_summaries(
        merged, phase=ExperimentPhase.done, creator_agent_id=None, q=None
    )
    running = filter_experiment_summaries(
        merged, phase=ExperimentPhase.running, creator_agent_id=None, q=None
    )
    assert len(done) == 1
    assert running == []


def test_sync_check_reports_phase_mismatch(tmp_path: Path) -> None:
    db_id = uuid.uuid4()
    _write_index(tmp_path, "forked", phase="done", projection_id=db_id)
    api = [
        _summary(
            id=db_id,
            phase=ExperimentPhase.running,
            current_plan_version=1,
            plan_file_path="map/experiments/forked/plan.md",
        )
    ]
    result = experiment_sync_check(api, tmp_path)
    assert result["ok"] is False
    assert result["matched"] == 1
    assert any(d["field"] == "phase" for d in result["diffs"])


def test_sync_check_zero_diff_and_fs_only_is_ok(tmp_path: Path) -> None:
    db_id = uuid.uuid4()
    _write_index(
        tmp_path,
        "aligned",
        phase="running",
        current_plan_version=3,
        projection_id=db_id,
    )
    _write_index(tmp_path, "history", phase="done")
    api = [
        _summary(
            id=db_id,
            phase=ExperimentPhase.running,
            current_plan_version=3,
            plan_file_path="map/experiments/aligned/plan.md",
        )
    ]
    result = experiment_sync_check(api, tmp_path)
    assert result["ok"] is True
    assert result["matched"] == 1
    assert result["diffs"] == []
    assert len(result["fs_only"]) == 1
    assert result["fs_only"][0]["slug"] == "history"


def test_sync_check_missing_dir_is_not_ok(tmp_path: Path) -> None:
    api = [
        _summary(
            id=uuid.uuid4(),
            plan_file_path="map/experiments/ghost/plan.md",
        )
    ]
    result = experiment_sync_check(api, tmp_path)
    assert result["ok"] is False
    assert len(result["missing_dir"]) == 1


def test_load_experiment_falls_back_to_fs(monkeypatch, tmp_path: Path) -> None:
    _write_index(tmp_path, "offline-show", phase="approved", title="Offline")
    monkeypatch.setattr("cli.experiment_fs.workspace_root", lambda: tmp_path)
    monkeypatch.setattr(
        "cli.commands.experiment._rid",
        lambda _c, _raw: experiment_id_for_slug("offline-show"),
    )

    class _Client:
        def get_experiment(self, _eid):
            raise MAPNotFoundError(404, "not in db")

    loaded = _load_experiment(_Client(), "offline-show")
    assert loaded.phase == ExperimentPhase.approved
    assert loaded.title == "Offline"
    assert loaded.blocked_on == "no_db_projection"


def test_fs_logs_from_log_md(tmp_path: Path) -> None:
    from cli.experiment_fs import fs_logs_for_ref

    _write_index(tmp_path, "with-log", phase="done")
    (tmp_path / "map" / "experiments" / "with-log" / "log.md").write_text(
        "# result\n\ndone.\n", encoding="utf-8"
    )
    logs = fs_logs_for_ref("with-log", tmp_path)
    assert len(logs) == 1
    assert logs[0].content_md.startswith("# result")
    assert logs[0].summary == "result"
    assert logs[0].file_path == "map/experiments/with-log/log.md"
