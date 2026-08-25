"""M1 A6: experiment --id 接受 slug / uuid5 / 存量 DB uuid。"""
from __future__ import annotations

import uuid
from pathlib import Path

from map_fs import experiment_id_for_slug, write_experiment_index

from cli.experiment_fs import find_fs_experiment, slug_from_plan_file_path


def test_uuid5_and_slug_resolve_same_directory(tmp_path: Path) -> None:
    slug = "route-demo"
    db_id = uuid.UUID("34840a7a-02d0-4def-835d-022396154bf2")
    write_experiment_index(
        tmp_path,
        slug,
        title="Route",
        creator="host",
        phase="running",
        executor="host",
        topic="src",
        projection_id=db_id,
    )
    fs_uuid5 = experiment_id_for_slug(slug)
    by_slug = find_fs_experiment(tmp_path, slug=slug)
    by_uuid5 = find_fs_experiment(tmp_path, ref=fs_uuid5)
    by_db = find_fs_experiment(tmp_path, ref=db_id)
    assert by_slug is not None and by_uuid5 is not None and by_db is not None
    assert by_slug.slug == by_uuid5.slug == by_db.slug == slug
    assert by_slug.projection_id == db_id
    assert by_uuid5.id == fs_uuid5


def test_slug_preferred_from_plan_path() -> None:
    assert slug_from_plan_file_path("map/experiments/foo-bar/plan.md") == "foo-bar"
    assert slug_from_plan_file_path("/abs/map/experiments/x/log.md") == "x"
