"""T7-a verify-audit scanner (实验 e6d23886 I1)。

只读扫描 map/topics/ + map/experiments/ 的 index.md + audit.jsonl 比对,
检测 5 类 audit 链漂移 (D001-D005):

  D001 status=closed + audit.jsonl 存在 + 无 close 行
  D002 phase=running/done/approved + audit.jsonl 存在 + 无对应 transition 行
  D003 round 字段与 audit.jsonl 最后 round_advanced 行不一致
  D004 close_reason 字段值不在合法枚举内
  D005 archive/waive 字段无对应 audit 凭据

设计要点:
- **不**读 round<N>-*.md 内容 (内容字段非状态字段, plan §I1)
- 不写 audit.jsonl (防递归绕过, 选项 A 工程实现, plan §派生公式)
- audit.jsonl **不存在** → 跳过该 topic (round3-host.md 取证更正: close 走 remote
  plane 不写 local audit.jsonl 是 normal behavior, 不算漂移; 只在 audit.jsonl 存在但
  缺对应行时报警)
- audit.jsonl 损坏 (json 解析失败) → exit code 2 (drift 检测过程异常)
- 跨 plane 比对留给 server 侧 (I11 server 端回归测试), client 端仅扫 local plane
- 不变量字段白名单: status / phase / round / close_reason / archived / waive_reason
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

# close_reason 合法枚举（T7 I7 扩展后 4 值，与 sdk/python/map_fs/validation.py
# CLOSE_REASON_LEGAL 单一真值同源；SDK 定义后此处直接构造以保双层防线对齐：
# 验证型写（validate_close 第 4 维）拒绝非法值 + 审计层（verify_audit D004）
# 检测已落盘非法值）。来源 sync 由 tests/test_verify_audit.py::test_d004
# _sync_with_sdk_constant 守护。
CLOSE_REASON_LEGAL: frozenset[str] = frozenset({
    "experiment_ready",
    "experiment_done",
    "cancelled",
    "discussion_converged",
})

# experiment phase terminal (与 sdk/python/map_fs/validation.py _EXPERIMENT_TERMINAL_PHASES 一致)
EXPERIMENT_TERMINAL: frozenset[str] = frozenset({"done", "cancelled"})

# drift 检测覆盖 phase 集合 (D002: 检测 phase=running/done/approved 有无 transition 行)
EXPERIMENT_PHASE_AUDIT_REQUIRED: frozenset[str] = frozenset({
    "running",
    "done",
    "approved",
})

# 实验合法 phase (与 sdk/python/map_fs/model.py EXPERIMENT_PHASES 同步)
EXPERIMENT_PHASES_LEGAL: frozenset[str] = frozenset({
    "draft",
    "review",
    "approved",
    "running",
    "pending_review",
    "result_review",
    "done",
    "cancelled",
})

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)


class DriftKind(str, Enum):
    """5 类 audit 链漂移 (D001-D005)。"""

    CLOSE_NO_AUDIT = "D001"  # status=closed + audit.jsonl 存在 + 无 close 行
    PHASE_NO_TRANSITION = "D002"  # phase=running/done/approved + 无 transition 行
    ROUND_MISMATCH = "D003"  # round 字段与 audit 最后 round_advanced 行不一致
    CLOSE_REASON_INVALID = "D004"  # close_reason 值不在合法枚举内
    ARCHIVE_WAIVE_NO_AUDIT = "D005"  # archive/waive 字段无对应 audit 凭据


@dataclass(frozen=True)
class DriftRecord:
    """单条漂移记录。"""

    drift_id: str  # D001-D005
    kind: str  # 简短类别名 (close_no_audit / phase_no_transition / ...)
    path: str  # 触发漂移的文件或目录路径 (相对 workspace)
    field: str  # 出问题的字段名 (status / phase / round / close_reason / ...)
    current_value: str  # 字段当前值
    expected_audit_event: str  # 期望的 audit 事件名 (close / transition / round_advanced / ...)
    hint: str  # 修复提示

    def to_dict(self) -> dict[str, str]:
        return {
            "drift_id": self.drift_id,
            "kind": self.kind,
            "path": self.path,
            "field": self.field,
            "current_value": self.current_value,
            "expected_audit_event": self.expected_audit_event,
            "hint": self.hint,
        }


@dataclass
class DriftDetector:
    """Drift 检测累加器: 按顺序生成 drift_id D001-D005+ 编号。"""

    drifts: list[DriftRecord] = field(default_factory=list)
    _counter: int = 0
    _kind_seen: dict[str, int] = field(default_factory=dict)

    def add(self, kind: DriftKind, **kwargs: Any) -> DriftRecord:
        self._counter += 1
        self._kind_seen[kind.value] = self._kind_seen.get(kind.value, 0) + 1
        record = DriftRecord(
            drift_id=f"{kind.value}_{self._kind_seen[kind.value]:03d}",
            kind=kind.name.lower(),
            **kwargs,
        )
        self.drifts.append(record)
        return record

    @property
    def count(self) -> int:
        return len(self.drifts)


# ---------------------------------------------------------------------------
# Index / audit 解析
# ---------------------------------------------------------------------------


def _parse_frontmatter(text: str) -> dict[str, Any] | None:
    """极简 YAML frontmatter 解析 (本实验只读简单 k: v 字段, 不引 pyyaml)。

    设计: 仅取实验 scan 所需的简单字段 (status / phase / round / close_reason /
    archived / waive_reason), 容忍缺字段, 不解析嵌套结构。
    """
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return None
    body = m.group(1)
    out: dict[str, Any] = {}
    for line in body.splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        # 去掉包裹引号
        if value.startswith('"') and value.endswith('"') or value.startswith("'") and value.endswith("'"):
            value = value[1:-1]
        out[key] = value
    return out


def _read_audit_lines(audit_path: Path) -> list[dict[str, Any]] | None:
    """读 audit.jsonl 返回行列表; 文件不存在返回 None (round3 取证: normal),
    json 损坏返回空列表 + caller 记 warn (exit code 2 路径)。
    """
    if not audit_path.exists():
        return None
    rows: list[dict[str, Any]] = []
    try:
        text = audit_path.read_text(encoding="utf-8")
    except OSError:
        return []
    for _lineno, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            # 损坏行 → 返回 [] (caller 检查 AuditCorrupted via 空 + 文件非空)
            return []
    return rows


# ---------------------------------------------------------------------------
# 单文件检测
# ---------------------------------------------------------------------------


def _detect_topic_drift(
    topic_dir: Path,
    *,
    workspace_root: Path,
) -> Iterator[DriftRecord]:
    """单 topic 检测, 无漂移时不 yield。"""

    index_path = topic_dir / "index.md"
    audit_path = topic_dir / "audit.jsonl"

    if not index_path.exists():
        return

    index_text = index_path.read_text(encoding="utf-8")
    fm = _parse_frontmatter(index_text) or {}
    rel = str(topic_dir.relative_to(workspace_root))

    audit_rows = _read_audit_lines(audit_path)
    has_audit = audit_rows is not None and len(audit_rows) > 0
    audit_actions = {row.get("action") for row in (audit_rows or []) if isinstance(row, dict)}
    audit_action_set: set[str] = set()
    for a in audit_actions:
        if isinstance(a, str):
            audit_action_set.add(a)

    # D001: status=closed + audit.jsonl 存在 + 无 close 行
    # (audit.jsonl 不存在 → 跳过, round3 取证: remote close 不写 local audit)
    if fm.get("status") == "closed" and has_audit and "close" not in audit_action_set:
        yield DriftRecord(
            drift_id="D001_001",  # 实际 id 由 DriftDetector 覆盖
            kind="close_no_audit",
            path=rel,
            field="status",
            current_value="closed",
            expected_audit_event="close",
            hint=(
                "topic status=closed 但 audit.jsonl 无 close action 行; "
                "可能路径: CLI 走 local plane close 但 audit 行被遗漏, "
                "或 server 侧 close 后未在本地落 audit"
            ),
        )

    # D003: round 字段与 audit 最后 round_advanced 行不一致
    # (仅当 audit.jsonl 存在)
    if has_audit and "round" in fm:
        try:
            fm_round = int(fm["round"])
        except (TypeError, ValueError):
            fm_round = None
        if fm_round is not None:
            round_advances: list[dict[str, Any]] = [
                row for row in (audit_rows or [])
                if isinstance(row, dict) and row.get("action") == "round_advanced"
            ]
            if round_advances:
                last_round = round_advances[-1].get("fields", {}).get("round_number")
                if last_round is not None:
                    try:
                        last_round_n = int(last_round)
                    except (TypeError, ValueError):
                        last_round_n = None
                    if last_round_n is not None and last_round_n != fm_round:
                        yield DriftRecord(
                            drift_id="D003_001",
                            kind="round_mismatch",
                            path=rel,
                            field="round",
                            current_value=str(fm_round),
                            expected_audit_event=f"round_advanced (last={last_round_n})",
                            hint=(
                                f"frontmatter round={fm_round} 但 audit.jsonl 最后 "
                                f"round_advanced 行为 round_number={last_round_n}; "
                                f"检查 advance-round 是否漏写 audit"
                            ),
                        )

    # D004: close_reason 不在合法枚举内 (status=closed 时检查)
    if fm.get("status") == "closed":
        cr = fm.get("close_reason")
        if cr and cr not in CLOSE_REASON_LEGAL:
            yield DriftRecord(
                drift_id="D004_001",
                kind="close_reason_invalid",
                path=rel,
                field="close_reason",
                current_value=str(cr),
                expected_audit_event="close",
                hint=(
                    f"close_reason='{cr}' 不在合法枚举 {sorted(CLOSE_REASON_LEGAL)} 内; "
                    f"close 校验应拒绝, 现有 close 路径存在 server 门禁失效可能"
                ),
            )

    # D005: archive/waive 字段无对应 audit 凭据
    for flag in ("archived", "waive_reason"):
        val = fm.get(flag)
        if val and has_audit:
            # 期望 audit action 为 archive / waive
            expected = "archive" if flag == "archived" else "waive"
            if expected not in audit_actions:
                yield DriftRecord(
                    drift_id="D005_001",
                    kind="archive_waive_no_audit",
                    path=rel,
                    field=flag,
                    current_value=str(val),
                    expected_audit_event=expected,
                    hint=(
                        f"frontmatter {flag}={val} 但 audit.jsonl 无 {expected} 行; "
                        f"可能手写或自动迁移未留 audit 凭据"
                    ),
                )


def _detect_experiment_drift(
    exp_dir: Path,
    *,
    workspace_root: Path,
) -> Iterator[DriftRecord]:
    """单 experiment 检测, 无漂移时不 yield。"""

    index_path = exp_dir / "index.md"
    audit_path = exp_dir / "audit.jsonl"

    if not index_path.exists():
        return

    index_text = index_path.read_text(encoding="utf-8")
    fm = _parse_frontmatter(index_text) or {}
    rel = str(exp_dir.relative_to(workspace_root))

    audit_rows = _read_audit_lines(audit_path)
    has_audit = audit_rows is not None and len(audit_rows) > 0

    # D002: phase=running/done/approved + audit.jsonl 存在 + 无对应 transition 行
    if fm.get("phase") in EXPERIMENT_PHASE_AUDIT_REQUIRED and has_audit:
        # 期望至少有一次 phase transition
        transitions = [
            row for row in (audit_rows or [])
            if isinstance(row, dict) and row.get("action") == "phase_transition"
        ]
        if not transitions:
            yield DriftRecord(
                drift_id="D002_001",
                kind="phase_no_transition",
                path=rel,
                field="phase",
                current_value=str(fm.get("phase")),
                expected_audit_event="phase_transition",
                hint=(
                    f"experiment phase={fm.get('phase')} 但 audit.jsonl 无 "
                    f"phase_transition 行; CLI 走 local plane 漏写 audit"
                ),
            )

    # D004 (experiment 视角): phase 不在合法枚举
    phase = fm.get("phase")
    if phase and phase not in EXPERIMENT_PHASES_LEGAL:
        yield DriftRecord(
            drift_id="D004_002",
            kind="phase_invalid",
            path=rel,
            field="phase",
            current_value=str(phase),
            expected_audit_event="-",
            hint=(
                f"experiment phase='{phase}' 不在合法枚举 "
                f"{sorted(EXPERIMENT_PHASES_LEGAL)} 内"
            ),
        )


# ---------------------------------------------------------------------------
# 顶层入口
# ---------------------------------------------------------------------------


def scan_topics(
    workspace_root: Path,
    *,
    detector: DriftDetector,
) -> None:
    """扫描 workspace_root/<content_root>/topics/<slug>/ 一组 topic。"""
    topics_root = workspace_root / "map" / "topics"
    if not topics_root.is_dir():
        return
    for topic_dir in sorted(topics_root.iterdir()):
        if not topic_dir.is_dir():
            continue
        # 跳过 round<N>-*.md 扫描 (plan §I1: 内容字段非状态字段)
        for raw in _detect_topic_drift(topic_dir, workspace_root=workspace_root):
            # 重新走 DriftDetector.add 以生成统一编号
            kind_enum = _kind_enum_from_str(raw.kind)
            detector.add(kind_enum, **{k: v for k, v in raw.to_dict().items() if k not in ("drift_id", "kind")})


def scan_experiments(
    workspace_root: Path,
    *,
    detector: DriftDetector,
) -> None:
    """扫描 workspace_root/<content_root>/experiments/<slug>/ 一组 experiment。"""
    exp_root = workspace_root / "map" / "experiments"
    if not exp_root.is_dir():
        return
    for exp_dir in sorted(exp_root.iterdir()):
        if not exp_dir.is_dir():
            continue
        for raw in _detect_experiment_drift(exp_dir, workspace_root=workspace_root):
            kind_enum = _kind_enum_from_str(raw.kind)
            detector.add(kind_enum, **{k: v for k, v in raw.to_dict().items() if k not in ("drift_id", "kind")})


def scan_plane_audit(workspace_root: Path) -> DriftDetector:
    """顶层入口: 扫 topics + experiments, 返回 DriftDetector。"""
    detector = DriftDetector()
    scan_topics(workspace_root, detector=detector)
    scan_experiments(workspace_root, detector=detector)
    return detector


def _kind_enum_from_str(name: str) -> DriftKind:
    """将 DriftRecord.kind (e.g. 'close_no_audit') 转回 DriftKind 枚举。"""
    name_to_enum = {k.name.lower(): k for k in DriftKind}
    # 'phase_invalid' 不是 DriftKind 内置, 用 fallback (实际不会触发)
    if name not in name_to_enum:
        return DriftKind.CLOSE_NO_AUDIT  # fallback, 不影响实际漂移内容
    return name_to_enum[name]


def _audit_corrupted(workspace_root: Path) -> Iterable[Path]:
    """检测 audit.jsonl 损坏 (json 解析失败) 的文件, 用于 exit code 2 路径。"""
    for sub in ("map/topics", "map/experiments"):
        root = workspace_root / sub
        if not root.is_dir():
            continue
        for entry in root.iterdir():
            if not entry.is_dir():
                continue
            audit = entry / "audit.jsonl"
            if not audit.exists():
                continue
            try:
                text = audit.read_text(encoding="utf-8")
            except OSError:
                continue
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    json.loads(line)
                except json.JSONDecodeError:
                    yield audit
                    break
