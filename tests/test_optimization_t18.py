"""优化任务 T18 的回归测试（见 docs/OPTIMIZATION-TASKS.md）。

FS 平面进程内缓存：指纹（topics/ + experiments/ 全文件的
(relpath, mtime_ns, size)）不变时复用上次解析的 FsPlane；任何文件
写入/新增/删除必然改变指纹并触发重扫；开关关闭或 reset 后行为退回
每次全量扫描。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from map_fs import write_round_comment, write_topic_index

from server.config import get_settings
from server.services import fs_source_service


class _FakeProject(SimpleNamespace):
    """plane_for_project 只读 workspace_path / content_root 两个字段。"""


def _project_at(tmp_path: Path) -> _FakeProject:
    return _FakeProject(workspace_path=str(tmp_path), content_root=None)


@pytest.fixture(autouse=True)
def _isolate_plane_cache():
    fs_source_service.reset_plane_cache()
    yield
    fs_source_service.reset_plane_cache()


def _patch_cache_enabled(monkeypatch, enabled: bool):
    patched = get_settings().model_copy(update={"fs_plane_cache_enabled": enabled})
    monkeypatch.setattr("server.config.get_settings", lambda: patched)


def test_plane_cache_reuses_parsed_plane_within_fingerprint(tmp_path: Path) -> None:
    write_topic_index(tmp_path, "cache-hit", title="Cache", creator="host")
    project = _project_at(tmp_path)

    first = fs_source_service.plane_for_project(project)
    second = fs_source_service.plane_for_project(project)

    assert first is second  # 指纹未变 → 同一解析实例，未重扫


def test_plane_cache_invalidates_on_new_file(tmp_path: Path) -> None:
    write_topic_index(tmp_path, "inv", title="Inv", creator="host")
    project = _project_at(tmp_path)

    first = fs_source_service.plane_for_project(project)
    assert len(first.topics[0].comments) == 0

    write_round_comment(tmp_path, "inv", round_number=1, persona="host", body="# 新评论")
    second = fs_source_service.plane_for_project(project)

    assert second is not first
    assert len(second.topics[0].comments) == 1


def test_plane_cache_invalidates_on_rewrite(tmp_path: Path) -> None:
    write_topic_index(tmp_path, "rw", title="RW", creator="host")
    write_round_comment(tmp_path, "rw", round_number=1, persona="host", body="v1")
    project = _project_at(tmp_path)

    first = fs_source_service.plane_for_project(project)
    assert first.topics[0].comments[0].content.strip() == "v1"

    write_round_comment(
        tmp_path, "rw", round_number=1, persona="host", body="v2", overwrite=True
    )
    second = fs_source_service.plane_for_project(project)

    assert second.topics[0].comments[0].content.strip() == "v2"


def test_plane_cache_reset_forces_rescan(tmp_path: Path) -> None:
    write_topic_index(tmp_path, "reset", title="Reset", creator="host")
    project = _project_at(tmp_path)

    first = fs_source_service.plane_for_project(project)
    fs_source_service.reset_plane_cache()
    second = fs_source_service.plane_for_project(project)

    assert second is not first


def test_plane_cache_disabled_by_settings(tmp_path: Path, monkeypatch) -> None:
    _patch_cache_enabled(monkeypatch, False)
    write_topic_index(tmp_path, "off", title="Off", creator="host")
    project = _project_at(tmp_path)

    first = fs_source_service.plane_for_project(project)
    second = fs_source_service.plane_for_project(project)

    assert second is not first  # 关闭后每次都全量重扫


def test_plane_cache_per_workspace_isolation(tmp_path: Path) -> None:
    workspace_a = tmp_path / "ws-a"
    workspace_b = tmp_path / "ws-b"
    write_topic_index(workspace_a, "topic-a", title="A", creator="host")
    write_topic_index(workspace_b, "topic-b", title="B", creator="host")

    plane_a1 = fs_source_service.plane_for_project(_project_at(workspace_a))
    plane_b = fs_source_service.plane_for_project(_project_at(workspace_b))
    plane_a2 = fs_source_service.plane_for_project(_project_at(workspace_a))

    assert plane_a1 is plane_a2
    assert plane_a1 is not plane_b
    assert plane_a1.topics[0].slug == "topic-a"
    assert plane_b.topics[0].slug == "topic-b"
