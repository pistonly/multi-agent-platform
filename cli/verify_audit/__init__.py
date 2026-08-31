"""T7-a verify-audit 只读检测层 (实验 e6d23886 I1)。

封装 scanner (本包) + output (I2) + cli 入口 (I3)。
"""
from cli.verify_audit.scanner import (
    DriftDetector,
    DriftKind,
    scan_experiments,
    scan_plane_audit,
    scan_topics,
)

__all__ = [
    "DriftDetector",
    "DriftKind",
    "scan_topics",
    "scan_experiments",
    "scan_plane_audit",
]
