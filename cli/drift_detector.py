"""Runtime skill drift detector — 实验 waker-runtime-skill-hotcheck I1+I2。

对比 ``.cursor/skills/<skill>/*`` 源与 runtime home 副本
``.claude/skills/<skill>/*``，发现漂移立即调用
:meth:`sync_runtime_skills` 重同步（同周期内同 skill 抑制）。

复用既有全量镜像同步（rmtree+copytree+孤儿清理）作为重同步原子动作，
不修改其语义，仅加触发时机与抖动抑制。

审计字段约定：

- ``MTIME_TOLERANCE_NS = 1_000_000``（1ms）跨平台快检容差
- ``SKIP_RESYNC_WINDOW_S = 1.0``（同周期内同 skill 抑制窗口）
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from cli.wake_backend import sync_runtime_skills

MTIME_TOLERANCE_NS = 1_000_000
SKIP_RESYNC_WINDOW_S = 1.0
HASH_CHUNK = 64 * 1024


@dataclass(frozen=True)
class DriftEntry:
    skill_relpath: str
    src_mtime_ns: int
    dst_mtime_ns: int
    src_size: int
    dst_size: int
    hash_mismatch: bool = False


@dataclass
class ResyncResult:
    ok: bool
    resynced_skills: list[str] = field(default_factory=list)
    duration_ms: int = 0
    error: str | None = None
    skipped_due_to_throttle: list[str] = field(default_factory=list)


class DriftDetector:
    """per-skill 漂移检测 + 立即重同步（同周期抖动抑制）。"""

    def __init__(
        self,
        *,
        source_root: Path,
        dest_root: Path,
        mtime_tolerance_ns: int = MTIME_TOLERANCE_NS,
        skip_window_s: float = SKIP_RESYNC_WINDOW_S,
        clock: callable = time.monotonic,
    ) -> None:
        self._source_root = source_root
        self._dest_root = dest_root
        self._mtime_tolerance_ns = mtime_tolerance_ns
        self._skip_window_s = skip_window_s
        self._clock = clock
        self._last_seen: dict[str, tuple[int, int]] = {}
        self._last_resync_at: dict[str, float] = {}
        self._initial_scanned = False

    def check_drift(self) -> list[DriftEntry]:
        """返回当前周期漂移列表；首次调用全 scan 填充缓存不报漂移。"""
        current: dict[str, tuple[int, int]] = {}
        entries: list[DriftEntry] = []

        for source_path in _iter_skill_files(self._source_root):
            rel = source_path.relative_to(self._source_root).as_posix()
            try:
                st = source_path.stat()
            except FileNotFoundError:
                continue
            current[rel] = (st.st_mtime_ns, st.st_size)
            dst = self._dest_root / rel
            entry = self._compare_one(rel, source_path, dst, st)
            if entry is not None:
                entries.append(entry)

        for rel, (_mtime_ns, _size) in list(self._last_seen.items()):
            if rel in current:
                continue
            dst = self._dest_root / rel
            if not dst.exists():
                try:
                    dst_st = dst.stat()
                except FileNotFoundError:
                    entries.append(
                        DriftEntry(
                            skill_relpath=rel,
                            src_mtime_ns=0,
                            dst_mtime_ns=0,
                            src_size=0,
                            dst_size=0,
                        )
                    )
                else:
                    entries.append(
                        DriftEntry(
                            skill_relpath=rel,
                            src_mtime_ns=0,
                            dst_mtime_ns=dst_st.st_mtime_ns,
                            src_size=0,
                            dst_size=dst_st.st_size,
                        )
                    )

        self._last_seen = current
        self._initial_scanned = True
        return entries

    def _compare_one(
        self,
        rel: str,
        src: Path,
        dst: Path,
        src_st,
    ) -> DriftEntry | None:
        if not self._initial_scanned:
            return None
        cached = self._last_seen.get(rel)
        if cached is None:
            return None
        cached_mtime, cached_size = cached
        if cached_mtime == src_st.st_mtime_ns and cached_size == src_st.st_size:
            return None
        if not dst.exists():
            return DriftEntry(
                skill_relpath=rel,
                src_mtime_ns=src_st.st_mtime_ns,
                dst_mtime_ns=0,
                src_size=src_st.st_size,
                dst_size=0,
            )
        try:
            dst_st = dst.stat()
        except FileNotFoundError:
            return DriftEntry(
                skill_relpath=rel,
                src_mtime_ns=src_st.st_mtime_ns,
                dst_mtime_ns=0,
                src_size=src_st.st_size,
                dst_size=0,
            )
        if _is_equivalent(src_st.st_mtime_ns, src_st.st_size, dst_st.st_mtime_ns, dst_st.st_size, self._mtime_tolerance_ns):
            return None
        if _hash_equal(src, dst):
            return None
        return DriftEntry(
            skill_relpath=rel,
            src_mtime_ns=src_st.st_mtime_ns,
            dst_mtime_ns=dst_st.st_mtime_ns,
            src_size=src_st.st_size,
            dst_size=dst_st.st_size,
            hash_mismatch=True,
        )

    def resync(
        self,
        drift_entries: Iterable[DriftEntry],
    ) -> ResyncResult:
        """立即重同步（同周期同 skill 抑制）。不抛异常。"""
        drift_list = list(drift_entries)
        if not drift_list:
            return ResyncResult(ok=True)
        now = self._clock()
        to_resync: list[str] = []
        throttled: list[str] = []
        for entry in drift_list:
            skill_key = entry.skill_relpath.split("/", 1)[0]
            last = self._last_resync_at.get(skill_key)
            if last is not None and (now - last) < self._skip_window_s:
                throttled.append(skill_key)
                continue
            to_resync.append(skill_key)
        if not to_resync:
            return ResyncResult(ok=True, skipped_due_to_throttle=throttled)
        t0 = self._clock()
        try:
            sync_runtime_skills(
                project_root=self._source_root.parent.parent,
                runtime_home=self._dest_root.parent.parent,
            )
        except Exception as exc:
            return ResyncResult(
                ok=False,
                resynced_skills=[],
                duration_ms=int((self._clock() - t0) * 1000),
                error=f"{type(exc).__name__}: {exc}",
                skipped_due_to_throttle=throttled,
            )
        for skill_key in to_resync:
            self._last_resync_at[skill_key] = now
        self._last_seen = {}
        return ResyncResult(
            ok=True,
            resynced_skills=to_resync,
            duration_ms=int((self._clock() - t0) * 1000),
            skipped_due_to_throttle=throttled,
        )


def _iter_skill_files(source_root: Path) -> Iterable[Path]:
    if not source_root.is_dir():
        return []
    out: list[Path] = []
    for skill_dir in sorted(source_root.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_md = skill_dir / "SKILL.md"
        if skill_md.is_file():
            out.append(skill_md)
        refs = skill_dir / "references"
        if refs.is_dir():
            for ref in sorted(refs.iterdir()):
                if ref.is_file():
                    out.append(ref)
    return out


def _is_equivalent(
    src_mtime_ns: int,
    src_size: int,
    dst_mtime_ns: int,
    dst_size: int,
    tolerance_ns: int,
) -> bool:
    if src_size != dst_size:
        return False
    return abs(src_mtime_ns - dst_mtime_ns) <= tolerance_ns


def _hash_equal(src: Path, dst: Path) -> bool:
    try:
        return _sha256(src) == _sha256(dst)
    except FileNotFoundError:
        return False


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(HASH_CHUNK)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()
