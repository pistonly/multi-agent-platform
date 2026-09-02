"""M1 helpers: experiment index.md 验证型写回 + slug/uuid5/DB uuid 路由。

DB 实验行仍是投影（锁 / 通知 / review item / token 仍在库）。退役条件：M2
``map experiment sync --check`` 对账零 diff 后，再考虑停 INSERT 主行。
show/list 以 FS index.md 为实验数据事实源；生命周期写仍走 API 门禁。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeVar

from map_client.project_config import (
    find_map_dir,  # noqa: F401 — 测试注入面（error envelope 等 monkeypatch）；无调用（实验 e7244a91 A5）
)
from map_fs import (
    commit_experiment_index_write,
    experiment_id_for_slug,
    parse_experiment_dir,
    scan_plane,
    slugify,
    topic_id_for_slug,
    write_experiment_index,
    write_experiment_review_yaml,
)
from map_types.enums import ExperimentMode, ExperimentPhase, PhaseOwner
from map_types.schemas import (
    ContentSourceMeta,
    ExperimentDetailRead,
    ExperimentLogRead,
    ExperimentSummaryRead,
    PlanVersionRead,
)

# 与 cli/commands/topic.py、server fs_source_service 同源：persona 名 → 展示用 uuid。
_PERSONA_NS = uuid.uuid5(uuid.NAMESPACE_URL, "map-fs-persona")
_PLAN_NS = uuid.uuid5(uuid.NAMESPACE_URL, "map-fs")
_FALLBACK_PROJECT_NS = uuid.uuid5(uuid.NAMESPACE_URL, "map-fs-project")

_SummaryT = TypeVar("_SummaryT", bound=ExperimentSummaryRead)


def _resolve_mode(raw: Any) -> ExperimentMode:
    """Best-effort ``ExperimentMode`` coercion for FS readback / overlay.

    The FS ``index.md`` frontmatter currently does not store ``mode`` (it
    lives in DB only), so callers without a real ``mode`` value get
    ``standard`` as the safe default. When ``mode`` is later added to the
    FS schema, callers should still tolerate raw strings here.
    """
    if isinstance(raw, ExperimentMode):
        return raw
    value = getattr(raw, "value", raw)
    try:
        return ExperimentMode(str(value))
    except ValueError:
        return ExperimentMode.standard


_PHASE_OWNER = {
    ExperimentPhase.draft: PhaseOwner.host,
    ExperimentPhase.review: PhaseOwner.reviewer,
    ExperimentPhase.pending_review: PhaseOwner.reviewer,
    ExperimentPhase.approved: PhaseOwner.host,
    ExperimentPhase.running: PhaseOwner.host,
    ExperimentPhase.result_review: PhaseOwner.reviewer,
    ExperimentPhase.done: PhaseOwner.host,
    ExperimentPhase.cancelled: PhaseOwner.host,
}


def _phase_owner_for(phase: ExperimentPhase, mode: ExperimentMode | str) -> PhaseOwner:
    """Phase owner lookup that honours ``ExperimentMode.direct``.

    CLI 是轻依赖面，不 import server 模块。本地表只覆盖两个 mode × phase
    交叉点；其它 (phase, mode) 组合走与 server 静态表一致的 ``_PHASE_OWNER``。
    任何新增 direct-mode 差异都必须同步 server
    ``server.services.phase_owner_resolver.owner_for``，避免漂移。
    """
    mode_value = mode.value if isinstance(mode, ExperimentMode) else str(mode)
    if mode_value == ExperimentMode.direct.value and phase == ExperimentPhase.running:
        return PhaseOwner.participant
    return _PHASE_OWNER.get(phase, PhaseOwner.host)

_CONTENT_ROOT = "map"


def workspace_root() -> Path | None:
    """实验 e7244a91（A1）：workspace 经 ProjectContext 单点解析；解析不出返回 None。

    调用方自行降级（本模块历史语义：None → 只读 API 视角），严格版错误
    由 ``cli.commands.fs._workspace`` 统一给出 bootstrap 指引。
    """
    from cli.project_context import optional_context

    context = optional_context()
    return None if context is None else context.workspace_root


def content_root_name(workspace: Path) -> str:
    """内容根名单点（context.config.content_root）；``workspace`` 参数仅为调用面保留。"""
    del workspace
    from cli.project_context import current_context

    return current_context().content_root


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


def _index_path(workspace: Path, fs: Any) -> Path:
    return workspace / fs.dir_path / "index.md"


def has_experiment_index(workspace: Path, fs: Any) -> bool:
    return _index_path(workspace, fs).is_file()


def persona_agent_id(name: str) -> uuid.UUID:
    return uuid.uuid5(_PERSONA_NS, name or "host")


def project_id_from_workspace(workspace: Path | None = None) -> uuid.UUID:
    root = workspace if workspace is not None else workspace_root()
    if root is None:
        return _FALLBACK_PROJECT_NS
    cfg = root / ".map" / "config.yaml"
    if not cfg.is_file():
        return _FALLBACK_PROJECT_NS
    import yaml

    data = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
    raw = data.get("project_id") if isinstance(data, dict) else None
    try:
        return uuid.UUID(str(raw))
    except (ValueError, TypeError):
        return _FALLBACK_PROJECT_NS


def _coerce_phase(raw: str) -> ExperimentPhase:
    try:
        return ExperimentPhase(str(raw).lower())
    except ValueError:
        return ExperimentPhase.draft


def _topic_id_from_fs(fs: Any) -> uuid.UUID | None:
    text = str(getattr(fs, "topic", "") or "").strip()
    if not text:
        return None
    try:
        return uuid.UUID(text)
    except ValueError:
        return topic_id_for_slug(text)


def _fs_source_meta(fs: Any) -> ContentSourceMeta:
    return ContentSourceMeta(
        content_source="fs",
        source_updated_at=getattr(fs, "updated_at", None),
        stale=False,
    )


def _review_yaml_count(workspace: Path, fs: Any) -> int:
    reviews = workspace / fs.dir_path / "reviews"
    if not reviews.is_dir():
        return 0
    return sum(1 for p in reviews.iterdir() if p.suffix.lower() in {".yaml", ".yml"})


def _plan_version_from_fs(
    workspace: Path, fs: Any, experiment_id: uuid.UUID
) -> PlanVersionRead | None:
    plan_path = getattr(fs, "plan_path", None)
    if not plan_path:
        return None
    path = workspace / plan_path
    if not path.is_file():
        return None
    created = fs.updated_at or fs.created_at or datetime.now(timezone.utc)
    return PlanVersionRead(
        id=uuid.uuid5(_PLAN_NS, f"plan:{fs.slug}:v{fs.current_plan_version}"),
        experiment_id=experiment_id,
        version=int(fs.current_plan_version or 1),
        content_md=path.read_text(encoding="utf-8"),
        author_agent_id=persona_agent_id(fs.creator),
        change_note=None,
        created_at=created,
    )


def iter_indexed_experiments(workspace: Path) -> list[Any]:
    """Only directories with index.md are FS authority (parser defaults otherwise)."""
    plane = scan_plane(workspace, content_root_name(workspace))
    return [exp for exp in plane.experiments if has_experiment_index(workspace, exp)]


def lookup_fs_for_ref(raw: str, workspace: Path | None = None) -> Any | None:
    root = workspace if workspace is not None else workspace_root()
    if root is None:
        return None
    text = raw.strip()
    try:
        ref_uuid = uuid.UUID(text)
    except ValueError:
        fs = find_fs_experiment(root, slug=text)
    else:
        fs = find_fs_experiment(root, ref=ref_uuid)
    if fs is None or not has_experiment_index(root, fs):
        return None
    return fs


def overlay_fs_authority(experiment: _SummaryT, workspace: Path | None = None) -> _SummaryT:
    """show/list：有 index.md 时 phase / plan_version / 标题以 FS 为准。锁字段保持 DB。"""
    root = workspace if workspace is not None else workspace_root()
    if root is None:
        return experiment
    slug = slug_from_plan_file_path(getattr(experiment, "plan_file_path", None))
    fs = find_fs_experiment(root, slug=slug, ref=experiment.id)
    if fs is None or not has_experiment_index(root, fs):
        return experiment
    phase = _coerce_phase(fs.phase)
    overlay_mode = _resolve_mode(getattr(experiment, "mode", ExperimentMode.standard))
    updates: dict[str, Any] = {
        "phase": phase,
        "current_plan_version": int(fs.current_plan_version or 1),
        "title": fs.title or experiment.title,
        "source": _fs_source_meta(fs),
        "phase_owner": _phase_owner_for(phase, overlay_mode),
    }
    if fs.description:
        updates["description"] = fs.description
    if fs.plan_path:
        updates["plan_file_path"] = fs.plan_path
    if fs.log_path:
        updates["log_file_path"] = fs.log_path
    topic_id = _topic_id_from_fs(fs)
    if topic_id is not None:
        updates["topic_id"] = topic_id
    if hasattr(experiment, "current_plan"):
        plan = _plan_version_from_fs(root, fs, experiment.id)
        if plan is not None:
            updates["current_plan"] = plan
            updates["plan_version_count"] = max(
                int(getattr(experiment, "plan_version_count", 0) or 0), 1
            )
    return experiment.model_copy(update=updates)


def fs_experiment_to_summary(
    fs: Any, project_id: uuid.UUID, workspace: Path
) -> ExperimentSummaryRead:
    """Synthesize a list/show record from index.md (no DB row required)."""
    now = datetime.now(timezone.utc)
    created = fs.created_at or fs.updated_at or now
    updated = fs.updated_at or created
    phase = _coerce_phase(fs.phase)
    exp_id = fs.projection_id or fs.id or experiment_id_for_slug(fs.slug)
    fs_only = fs.projection_id is None
    # FS-only synthesis has no DB-derived mode; standard is the safe
    # default and matches the historical contract. Direct-mode FS-only
    # records will be re-overlaid by the API path on top of this.
    fs_mode = ExperimentMode.standard
    return ExperimentSummaryRead(
        id=exp_id,
        project_id=project_id,
        creator_agent_id=persona_agent_id(fs.creator),
        executor_agent_id=persona_agent_id(fs.executor) if fs.executor else None,
        title=fs.title,
        description=fs.description or None,
        phase=phase,
        mode=fs_mode,
        current_plan_version=int(fs.current_plan_version or 1),
        topic_id=_topic_id_from_fs(fs),
        warnings=["fs_only_no_db_projection"] if fs_only else [],
        created_at=created,
        updated_at=updated,
        plan_file_path=fs.plan_path,
        log_file_path=fs.log_path,
        source=_fs_source_meta(fs),
        actions=[],
        blocked_on="no_db_projection" if fs_only else None,
        phase_owner=_phase_owner_for(phase, fs_mode),
        informational_only=fs_only,
    )


def fs_experiment_to_detail(
    fs: Any, project_id: uuid.UUID, workspace: Path
) -> ExperimentDetailRead:
    summary = fs_experiment_to_summary(fs, project_id, workspace)
    plan = _plan_version_from_fs(workspace, fs, summary.id)
    return ExperimentDetailRead(
        **summary.model_dump(),
        current_plan=plan,
        plan_version_count=1 if plan is not None else 0,
        review_count=_review_yaml_count(workspace, fs),
        acceptance_status=[],
    )


def should_scan_local_experiments(
    project: uuid.UUID | None,
    project_key: str | None,
    resolved_pid: uuid.UUID,
) -> bool:
    """只在列出当前 workspace 所属项目时合并本地 map/experiments。"""
    import yaml

    workspace = workspace_root()
    if workspace is None:
        return False
    if project is None and project_key is None:
        return True
    map_cfg = workspace / ".map" / "config.yaml"
    if not map_cfg.is_file():
        return False
    try:
        data = yaml.safe_load(map_cfg.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return False
    if not isinstance(data, dict):
        return False
    cfg_id = data.get("project_id")
    if cfg_id and str(cfg_id) == str(resolved_pid):
        return True
    cfg_key = data.get("project_key")
    return bool(project_key) and cfg_key == project_key


def merge_experiment_summaries(
    fs_exps: list[Any],
    api_items: list[ExperimentSummaryRead],
    project_id: uuid.UUID,
    workspace: Path,
) -> list[ExperimentSummaryRead]:
    """FS-only extras first (like topic list), then API rows with FS overlay."""
    api_ids = {item.id for item in api_items}
    api_slugs = {
        slug_from_plan_file_path(getattr(item, "plan_file_path", None))
        for item in api_items
    }
    api_slugs.discard(None)
    extras: list[ExperimentSummaryRead] = []
    for fs in fs_exps:
        if fs.projection_id in api_ids or fs.id in api_ids or fs.slug in api_slugs:
            continue
        extras.append(fs_experiment_to_summary(fs, project_id, workspace))
    overlaid = [overlay_fs_authority(item, workspace) for item in api_items]
    return extras + overlaid


def filter_experiment_summaries(
    items: list[ExperimentSummaryRead],
    *,
    phase: ExperimentPhase | None,
    creator_agent_id: uuid.UUID | None,
    q: str | None,
) -> list[ExperimentSummaryRead]:
    needle = q.lower() if q else None
    out: list[ExperimentSummaryRead] = []
    for item in items:
        if phase is not None and item.phase != phase:
            continue
        if creator_agent_id is not None and item.creator_agent_id != creator_agent_id:
            continue
        if needle:
            slug = slug_from_plan_file_path(item.plan_file_path) or ""
            haystack = f"{item.title} {slug} {item.id}".lower()
            if needle not in haystack:
                continue
        out.append(item)
    return out


def fs_logs_for_ref(raw: str, workspace: Path | None = None) -> list[ExperimentLogRead]:
    """FS-only ``experiment logs``: one record from log.md when the file exists."""
    from map_fs import make_excerpt, parse_front_matter

    root = workspace if workspace is not None else workspace_root()
    if root is None:
        return []
    fs = lookup_fs_for_ref(raw, root)
    if fs is None or not fs.log_path:
        return []
    path = root / fs.log_path
    if not path.is_file():
        return []
    content = path.read_text(encoding="utf-8")
    meta, body = parse_front_matter(content)
    created = fs.updated_at or fs.created_at or datetime.now(timezone.utc)
    exp_id = fs.projection_id or fs.id
    return [
        ExperimentLogRead(
            id=uuid.uuid5(_PLAN_NS, f"log:{fs.slug}"),
            experiment_id=exp_id,
            author_agent_id=persona_agent_id(fs.executor or fs.creator),
            summary=make_excerpt(body) or str(meta.get("title") or fs.title),
            content_md=content,
            file_path=fs.log_path,
            metadata_json=None,
            created_at=created,
        )
    ]


def lifecycle_missing_projection_message(raw: str) -> str | None:
    """FS-only or stale projection: lifecycle still needs a DB row."""
    workspace = workspace_root()
    if workspace is None:
        return None
    fs = lookup_fs_for_ref(raw, workspace)
    if fs is None:
        return None
    if fs.projection_id is None:
        return (
            f"experiment '{fs.slug}' is FS-only (no projection_id in index.md); "
            "lifecycle commands still require a DB projection for locks/notifications. "
            "Create via `map experiment create` or restore projection_id after "
            "`map experiment sync --check`."
        )
    return (
        f"DB projection {fs.projection_id} not found for '{fs.slug}'. "
        "index.md projection_id is stale; run `map experiment sync --check`."
    )


def experiment_sync_check(
    api_items: list[ExperimentSummaryRead],
    workspace: Path | None = None,
) -> dict[str, Any]:
    """Diff DB projection vs index.md. Does not rewrite files."""
    root = workspace if workspace is not None else workspace_root()
    if root is None:
        return {
            "ok": False,
            "error": ".map/ not found; cannot scan local experiments",
            "matched": 0,
            "diffs": [],
            "repairs": [],
            "fs_only": [],
            "db_only": [],
            "missing_dir": [],
        }
    fs_exps = iter_indexed_experiments(root)
    by_id: dict[uuid.UUID, ExperimentSummaryRead] = {item.id: item for item in api_items}
    by_slug: dict[str, ExperimentSummaryRead] = {}
    for item in api_items:
        slug = slug_from_plan_file_path(getattr(item, "plan_file_path", None))
        if slug:
            by_slug[slug] = item

    diffs: list[dict[str, Any]] = []
    repairs: list[dict[str, Any]] = []
    fs_only: list[dict[str, Any]] = []
    matched_api_ids: set[uuid.UUID] = set()
    matched = 0

    for fs in fs_exps:
        api = None
        if fs.projection_id is not None:
            api = by_id.get(fs.projection_id)
        if api is None:
            api = by_id.get(fs.id)
        if api is None:
            api = by_slug.get(fs.slug)
        if api is None:
            fs_only.append(
                {
                    "slug": fs.slug,
                    "phase": fs.phase,
                    "id": str(fs.id),
                    "projection_id": str(fs.projection_id) if fs.projection_id else None,
                }
            )
            continue
        matched += 1
        matched_api_ids.add(api.id)
        fs_phase = _coerce_phase(fs.phase).value
        db_phase = str(getattr(api.phase, "value", api.phase))
        if fs_phase != db_phase:
            diffs.append(
                {
                    "slug": fs.slug,
                    "field": "phase",
                    "fs": fs_phase,
                    "db": db_phase,
                }
            )
        fs_ver = int(fs.current_plan_version or 1)
        db_ver = int(api.current_plan_version or 1)
        if fs_ver != db_ver:
            diffs.append(
                {
                    "slug": fs.slug,
                    "field": "current_plan_version",
                    "fs": fs_ver,
                    "db": db_ver,
                }
            )
        if fs.projection_id is None:
            repairs.append(
                {
                    "slug": fs.slug,
                    "kind": "missing_projection_id",
                    "db_id": str(api.id),
                }
            )
        elif fs.projection_id != api.id:
            diffs.append(
                {
                    "slug": fs.slug,
                    "field": "projection_id",
                    "fs": str(fs.projection_id),
                    "db": str(api.id),
                }
            )

    db_only: list[dict[str, Any]] = []
    missing_dir: list[dict[str, Any]] = []
    experiments_root = root / content_root_name(root) / "experiments"
    for item in api_items:
        if item.id in matched_api_ids:
            continue
        slug = slug_from_plan_file_path(getattr(item, "plan_file_path", None))
        record = {
            "id": str(item.id),
            "title": item.title,
            "phase": str(getattr(item.phase, "value", item.phase)),
            "plan_file_path": item.plan_file_path,
        }
        if slug:
            exp_dir = experiments_root / slug
            if not exp_dir.is_dir():
                missing_dir.append(record)
                continue
        db_only.append(record)

    ok = not diffs and not repairs and not missing_dir
    return {
        "ok": ok,
        "matched": matched,
        "diffs": diffs,
        "repairs": repairs,
        "fs_only": fs_only,
        "db_only": db_only,
        "missing_dir": missing_dir,
        "authority": "index.md after validated write",
    }


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
