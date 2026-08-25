"""M1 helpers: experiment index.md 验证型写回 + slug/uuid5/DB uuid 路由。

DB 实验行仍是投影（锁 / 通知 / review item / token 仍在库）。退役条件：M2
``map experiment sync --check`` 对账零 diff 后，再考虑停 INSERT 主行。
show/list 以 FS index.md 的 phase / current_plan_version 为准（有目录时）。
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from map_client.project_config import find_map_dir
from map_fs import (
    commit_experiment_index_write,
    parse_experiment_dir,
    scan_plane,
    slugify,
    write_experiment_index,
    write_experiment_review_yaml,
)
from map_types.enums import ExperimentPhase
from map_types.schemas import ExperimentSummaryRead

_CONTENT_ROOT = "map"


def workspace_root() -> Path | None:
    map_dir = find_map_dir(None)
    return None if map_dir is None else map_dir.parent


def content_root_name(workspace: Path) -> str:
    cfg = workspace / ".map" / "config.yaml"
    if cfg.is_file():
        import yaml

        data = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
        if isinstance(data, dict) and data.get("content_root"):
            return str(data["content_root"])
    return _CONTENT_ROOT


def slug_from_plan_file_path(path: str | None) -> str | None:
    if not path:
        return None
    parts = Path(path).parts
    if "experiments" in parts:
        idx = list(parts).index("experiments")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    return None


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))


def find_fs_experiment(workspace: Path, *, slug: str | None = None, ref: uuid.UUID | None = None):
    root = content_root_name(workspace)
    if slug:
        parsed = parse_experiment_dir(
            workspace / root / "experiments" / slug, workspace
        )
        if parsed is not None:
            return parsed
    plane = scan_plane(workspace, root)
    if slug:
        for exp in plane.experiments:
            if exp.slug == slug:
                return exp
    if ref is not None:
        for exp in plane.experiments:
            if exp.id == ref or exp.projection_id == ref:
                return exp
    return None


def projection_id_for_fs_ref(raw: str) -> uuid.UUID | None:
    """slug → FS 优先：目录存在则返回 projection_id（或 uuid5 若尚未投影）。"""
    workspace = workspace_root()
    if workspace is None:
        return None
    text = raw.strip()
    try:
        ref_uuid = uuid.UUID(text)
    except ValueError:
        ref_uuid = None
        fs = find_fs_experiment(workspace, slug=text)
    else:
        fs = find_fs_experiment(workspace, ref=ref_uuid)
    if fs is None:
        return None
    return fs.projection_id or fs.id


def looks_like_hex_prefix(raw: str) -> bool:
    text = raw.strip().lower().replace("-", "")
    return 8 <= len(text) < 32 and all(c in "0123456789abcdef" for c in text)


def overlay_fs_authority(experiment: ExperimentSummaryRead) -> ExperimentSummaryRead:
    """show/list：有 index.md 时 phase / plan_version 以 FS 为准。"""
    workspace = workspace_root()
    if workspace is None:
        return experiment
    slug = slug_from_plan_file_path(getattr(experiment, "plan_file_path", None))
    fs = find_fs_experiment(workspace, slug=slug, ref=experiment.id)
    if fs is None:
        return experiment
    index_path = workspace / fs.dir_path / "index.md"
    if not index_path.is_file():
        return experiment
    updates: dict[str, Any] = {}
    if fs.phase in {p.value for p in ExperimentPhase}:
        updates["phase"] = ExperimentPhase(fs.phase)
    if fs.current_plan_version:
        updates["current_plan_version"] = fs.current_plan_version
    if not updates:
        return experiment
    return experiment.model_copy(update=updates)


def preflight_index(experiment: Any, expected_from_phase: str, target_phase: str) -> None:
    """API 调用前：若 index.md 存在，手改 phase 在此拦截（不打生命周期接口）。"""
    from map_fs import validate_experiment_index_file

    workspace = workspace_root()
    if workspace is None:
        return
    slug = slug_from_plan_file_path(getattr(experiment, "plan_file_path", None))
    if slug is None:
        fs = find_fs_experiment(workspace, ref=getattr(experiment, "id", None))
        slug = fs.slug if fs is not None else None
    if slug is None:
        return
    root = content_root_name(workspace)
    index = workspace / root / "experiments" / slug / "index.md"
    if not index.is_file():
        return
    validate_experiment_index_file(
        workspace,
        slug,
        content_root=root,
        expected_from_phase=expected_from_phase,
        target_phase=target_phase,
        mode=_enum_value(getattr(experiment, "mode", "standard")),
    )


def write_index_after_create(
    created: Any,
    *,
    plan_file_path: str | None,
    creator_persona: str = "host",
    topic_ref: str = "",
) -> Path | None:
    workspace = workspace_root()
    if workspace is None:
        return None
    slug = slug_from_plan_file_path(plan_file_path) or slugify(str(created.title))
    phase = _enum_value(created.phase)
    return write_experiment_index(
        workspace,
        slug,
        title=created.title,
        creator=creator_persona,
        phase=phase,
        current_plan_version=int(getattr(created, "current_plan_version", 1) or 1),
        executor="",
        topic=topic_ref,
        description=getattr(created, "description", None) or "",
        projection_id=created.id,
        content_root=content_root_name(workspace),
    )


def writeback_after_transition(
    experiment: Any,
    *,
    expected_from_phase: str,
    target_phase: str,
    executor_persona: str | None = None,
    review_payload: dict[str, Any] | None = None,
    review_filename: str | None = None,
) -> None:
    workspace = workspace_root()
    if workspace is None:
        return
    slug = slug_from_plan_file_path(getattr(experiment, "plan_file_path", None))
    if slug is None:
        fs = find_fs_experiment(workspace, ref=experiment.id)
        slug = fs.slug if fs is not None else slugify(str(experiment.title))
    root = content_root_name(workspace)
    index = workspace / root / "experiments" / slug / "index.md"
    if not index.is_file():
        write_experiment_index(
            workspace,
            slug,
            title=experiment.title,
            creator="host",
            phase=expected_from_phase,
            current_plan_version=int(getattr(experiment, "current_plan_version", 1) or 1),
            executor=executor_persona or "",
            topic="",
            description=getattr(experiment, "description", None) or "",
            projection_id=experiment.id,
            content_root=root,
        )
    commit_experiment_index_write(
        workspace,
        slug,
        expected_from_phase=expected_from_phase,
        target_phase=target_phase,
        current_plan_version=int(getattr(experiment, "current_plan_version", 1) or 1),
        executor=executor_persona,
        mode=_enum_value(getattr(experiment, "mode", "standard")),
        content_root=root,
    )
    if review_payload is not None:
        write_experiment_review_yaml(
            workspace,
            slug,
            review_filename or "review.yaml",
            review_payload,
            content_root=root,
        )


def topic_ref_for_create(topic_id: uuid.UUID | str | None) -> str:
    if topic_id is None:
        return ""
    workspace = workspace_root()
    if workspace is not None:
        from map_fs import scan_plane, topic_id_for_slug

        try:
            tid = topic_id if isinstance(topic_id, uuid.UUID) else uuid.UUID(str(topic_id))
        except ValueError:
            return str(topic_id)
        for topic in scan_plane(workspace, content_root_name(workspace)).topics:
            if topic.id == tid or topic_id_for_slug(topic.slug) == tid:
                return topic.slug
    return str(topic_id)
