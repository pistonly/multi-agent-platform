"""FS plane 加载与缓存 — T45 拆分自 fs_source_service.py。

content_root 解析、plane 指纹缓存（``_plane_cache``，LRU 上限）、
``plane_for_project`` 全量扫描入口、``content_source_meta`` /
``fs_plane_status``（部署矩阵握手）。读视图层见 fs_topic_view，写门禁
留守宿主。
"""
from __future__ import annotations

import logging
import re
import threading
import uuid
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from map_fs import (
    AckPendingError,
    FsPlane,
    InvalidCloseNoteError,
    InvalidCloseReasonError,
    OpenActionItemsError,
    TopicStateError,
    scan_plane,
)
from map_types.schemas.content_source import ContentSourceMeta
from map_types.schemas.fs import (
    FsPlaneStatusRead,
)
from sqlalchemy.orm import Session

from server.config import get_settings
from server.domain.models import Project

# T06（2026-08）：投影缓存存储簇（push 全量 / delta 增量 / apply-fields
# 修补 / 行读取与超限防线）已拆至 ``fs_projection_store``。此处 re-export
# 维持既有导入路径（api/fs.py 的 ``fs_svc.upsert_fs_projection``、测试的
# ``_payload_size_ok`` / ``_is_project_host`` 均从本模块取）；依赖方向为
# fs_source_service --top--> fs_projection_store --lazy--> fs_source_service
# （后者的 content_root_name / workspace_fs_available / content_source_meta
# 三个 scan 侧 helper），无导入环。
from server.services.fs_projection_store import (  # noqa: F401
    FsProjectionTooLargeError,
    _is_project_host,
    _payload_size_ok,
    apply_fields_to_projection,
    apply_fs_projection_delta,
    get_fs_projection,
    projection_inventory,
    projection_meta,
    projection_payload_experiments,
    projection_payload_topics,
    upsert_fs_projection,
)

logger = logging.getLogger(__name__)

_PERSONA_NS = uuid.uuid5(uuid.NAMESPACE_URL, "map-fs-persona")
_ROUND_STR_RE = re.compile(r"^round(\d+)$")


class FsTopicNotFoundError(Exception):
    """话题文件夹不存在。"""


# 门禁异常类定义在共享层 map_fs.validation（单一真值，CLI local plane 同源
# 消费）。这里别名到同一类对象：``server/api/fs.py`` 的 isinstance 映射与
# 409 消息格式完全不变。属性/消息契约见 map_fs.validation 对应类 docstring。
FsAckPendingError = AckPendingError
FsInvalidCloseNoteError = InvalidCloseNoteError
FsInvalidCloseReasonError = InvalidCloseReasonError
FsOpenActionItemsError = OpenActionItemsError
FsStateError = TopicStateError


class FsPlaneUnavailableError(Exception):
    """server 看不到 workspace 且无投影缓存/evidence 可用。

    部署矩阵显式化的一部分：旧实现里这表现为"静默空列表"或 404，现在
    抛出带修复指引的错误（挂载 workspace / map fs push / 携带 evidence）。
    """


# ---------------------------------------------------------------------------
# 基础：workspace / plane / 可达性
# ---------------------------------------------------------------------------


def content_root_name(project: Project | None = None) -> str:
    """Project-level content root; settings default is only for missing rows."""
    if project is not None and getattr(project, "content_root", None):
        return str(project.content_root)
    return get_settings().content_root


# T18（2026-08）：本地 FS 平面进程内缓存。键 = (workspace, content_root)，
# 值 = (文件指纹, FsPlane)。指纹覆盖 scan_plane 实际读取的两棵树
# （topics/ + experiments/）下全部文件的 (相对路径, mtime_ns, size, ino)——
# 任何写路径（CLI 落盘 / 验证型写回 / Agent 直接编辑）都会改变 mtime、
# size 或 inode（原子 replace 换 inode）。仅 (mtime, size) 在粗粒度时间戳
# 文件系统上会漏掉「同秒、同大小覆盖写入」，inode 补上这条缺口。
# 指纹采集只 stat 不读内容，远廉价于全量解析。FsPlane 及其
# topic/experiment 对象在 server 侧只读消费（全部读取方只构建 Read
# 模型 / derive_work），共享同一实例安全。
_PLANE_CACHE_MAX_ENTRIES = 8
_PlaneFingerprint = tuple[tuple[str, int, int, int], ...]
_plane_cache: OrderedDict[tuple[str, str], tuple[_PlaneFingerprint, FsPlane]] = OrderedDict()
_plane_cache_lock = threading.Lock()


def reset_plane_cache() -> None:
    """清空 FS 平面缓存（测试隔离钩子 / 运维排查用）。"""
    with _plane_cache_lock:
        _plane_cache.clear()


def _plane_fingerprint(workspace: Path, content_root: str) -> _PlaneFingerprint | None:
    """Collect (relpath, mtime_ns, size, ino) for every file scan_plane would read."""
    entries: list[tuple[str, int, int, int]] = []
    root = workspace / content_root
    for subdir in ("topics", "experiments"):
        base = root / subdir
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            try:
                st = path.stat()
            except OSError:
                continue  # 竞态：扫描期间文件被删——scan_plane 同样会跳过
            if path.is_file():
                # relpath 相对 content_root，避免 topics/ 与 experiments/ 下
                # 同名文件在指纹里撞车；st_ino 让原子 replace 在 mtime 不变
                # （1s 粒度 FS / 同秒覆盖）时仍能失效缓存。
                entries.append(
                    (str(path.relative_to(root)), st.st_mtime_ns, st.st_size, st.st_ino)
                )
    return tuple(entries)


def plane_for_project(project: Project) -> FsPlane:
    from server.config import get_settings

    workspace = Path(project.workspace_path)
    root_name = content_root_name(project)
    cacheable = get_settings().fs_plane_cache_enabled
    fingerprint = _plane_fingerprint(workspace, root_name) if cacheable else None
    if fingerprint:
        key = (str(workspace), root_name)
        with _plane_cache_lock:
            hit = _plane_cache.get(key)
        if hit is not None and hit[0] == fingerprint:
            return hit[1]
    plane = scan_plane(workspace, root_name)
    if fingerprint:
        key = (str(workspace), root_name)
        with _plane_cache_lock:
            _plane_cache[key] = (fingerprint, plane)
            while len(_plane_cache) > _PLANE_CACHE_MAX_ENTRIES:
                _plane_cache.popitem(last=False)
    return plane


def workspace_fs_available(project: Project) -> bool:
    """server 能否直接读到该 project 的内容根目录。"""
    return (Path(project.workspace_path) / content_root_name(project)).is_dir()


def content_source_meta(db: Session, project: Project) -> ContentSourceMeta:
    """Build the unified origin envelope for this project's FS plane."""
    content_root_exists = workspace_fs_available(project)
    row = get_fs_projection(db, project)
    now = datetime.now(timezone.utc)
    if content_root_exists:
        return ContentSourceMeta(
            content_source="local-fs",
            source_revision="local-scan",
            source_updated_at=now,
            stale=False,
        )
    if row is None:
        return ContentSourceMeta(
            content_source="none",
            stale=True,
            stale_reason="no projection",
        )
    stale = False
    stale_reason = None
    sla = project.fs_freshness_sla_seconds
    if sla is not None and row.pushed_at is not None:
        pushed = row.pushed_at
        if pushed.tzinfo is None:
            pushed = pushed.replace(tzinfo=timezone.utc)
        if now - pushed > timedelta(seconds=sla):
            stale = True
            stale_reason = "freshness_sla_exceeded"
    return ContentSourceMeta(
        content_source="fs-projection",
        source_revision=str(row.revision),
        source_content_hash=row.content_hash,
        source_updated_at=row.pushed_at,
        stale=stale,
        stale_reason=stale_reason,
    )


def fs_plane_status(db: Session, project: Project) -> FsPlaneStatusRead:
    """部署矩阵探测握手：local-fs / projection-cache / detached 三态。"""
    workspace = Path(project.workspace_path)
    workspace_exists = workspace.is_dir()
    content_root_exists = workspace_fs_available(project)
    row = get_fs_projection(db, project)
    source = content_source_meta(db, project)
    if content_root_exists:
        mode = "local-fs"
        hint = ""
    elif row is not None:
        mode = "projection-cache"
        hint = (
            "workspace 不可达，读路径回退到 map sync publish 的投影缓存；"
            "验证型写走 validate → 本地写回 → commit。写文件后 CLI 会自动增量同步。"
        )
    else:
        mode = "detached"
        hint = (
            "server 看不到 workspace（远程/容器部署），FS plane 对 server 不可见："
            "map/ 话题不会出现在列表与 work 待办中。修复：执行 `map sync publish` "
            "（或兼容别名 `map sync push`）上行投影缓存。"
        )
    return FsPlaneStatusRead(
        workspace_path=project.workspace_path,
        content_root=content_root_name(project),
        workspace_exists=workspace_exists,
        content_root_exists=content_root_exists,
        mode=mode,
        projection_pushed_at=row.pushed_at if row is not None else None,
        projection_revision=row.revision if row is not None else None,
        publisher_agent_id=row.publisher_agent_id if row is not None else None,
        consistency_model=("single-publisher-eventual" if row is not None else None),
        hint=hint,
        source=source,
    )
