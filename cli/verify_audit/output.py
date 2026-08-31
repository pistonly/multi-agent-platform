"""T7-a verify-audit output 双格式渲染 (实验 e6d23886 I2)。

- `--format json` (默认): JSON 结构化输出, waker 集成 + grep/jq 消费
- `--format human`: 人类可读表格
- exit code:
    0 = 干净 (无漂移)
    1 = 有漂移
    2 = 校验过程异常 (如 audit.jsonl 损坏无法解析)

drift_id 编号格式: D001, D002, ... (每类独立计数, 由 DriftDetector 累加器维护)。
"""
from __future__ import annotations

import json
from typing import Any

from cli.verify_audit.scanner import DriftDetector

# ---------------------------------------------------------------------------
# JSON 输出 (默认, waker 集成)
# ---------------------------------------------------------------------------


def render_json(
    detector: DriftDetector,
    *,
    workspace_root: str,
    corrupted_files: list[str] | None = None,
) -> dict[str, Any]:
    """生成结构化 JSON 输出 dict (供 json.dumps + stdout 用)。"""
    return {
        "status": "drift_detected" if detector.count > 0 else "clean",
        "workspace": workspace_root,
        "drift_count": detector.count,
        "drifts": [d.to_dict() for d in detector.drifts],
        "corrupted_audit_files": corrupted_files or [],
    }


# ---------------------------------------------------------------------------
# Human 输出 (表格, 终端可读)
# ---------------------------------------------------------------------------


def render_human(
    detector: DriftDetector,
    *,
    workspace_root: str,
    corrupted_files: list[str] | None = None,
) -> str:
    """生成人类可读 markdown 表格 + 状态总结。"""
    lines: list[str] = []
    if detector.count == 0:
        lines.append(f"verify-audit: clean (workspace={workspace_root}, 0 drifts)")
    else:
        lines.append(
            f"verify-audit: drift_detected "
            f"(workspace={workspace_root}, drift_count={detector.count})"
        )
        lines.append("")
        # 表头
        headers = ("drift_id", "kind", "path", "field", "current", "expected_event")
        lines.append("| " + " | ".join(headers) + " |")
        lines.append("|" + "|".join(["---"] * len(headers)) + "|")
        for d in detector.drifts:
            row = (
                d.drift_id,
                d.kind,
                d.path,
                d.field,
                d.current_value[:40],
                d.expected_audit_event[:30],
            )
            lines.append("| " + " | ".join(row) + " |")
        # hint 列表
        lines.append("")
        lines.append("Hints:")
        for d in detector.drifts:
            lines.append(f"  - {d.drift_id}: {d.hint}")

    if corrupted_files:
        lines.append("")
        lines.append(f"[ERROR] {len(corrupted_files)} audit.jsonl corrupted:")
        for f in corrupted_files:
            lines.append(f"  - {f}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 顶层入口: 渲染并打印
# ---------------------------------------------------------------------------


def print_output(
    detector: DriftDetector,
    *,
    fmt: str = "json",
    workspace_root: str,
    corrupted_files: list[str] | None = None,
) -> None:
    """按 fmt 渲染输出到 stdout。"""
    if fmt == "json":
        payload = render_json(
            detector, workspace_root=workspace_root, corrupted_files=corrupted_files
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif fmt == "human":
        text = render_human(
            detector, workspace_root=workspace_root, corrupted_files=corrupted_files
        )
        print(text)
    else:
        # 未知 fmt 走 json fallback
        payload = render_json(
            detector, workspace_root=workspace_root, corrupted_files=corrupted_files
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))


def compute_exit_code(
    detector: DriftDetector,
    *,
    corrupted_files: list[str] | None = None,
) -> int:
    """根据检测结果计算 exit code。

    - 0: 无漂移 + 无损坏
    - 1: 有漂移 (drifts 列表非空)
    - 2: 校验过程异常 (audit.jsonl 损坏)
    """
    if corrupted_files:
        return 2
    if detector.count > 0:
        return 1
    return 0
