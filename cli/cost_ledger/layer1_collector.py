"""T5-B Layer 1 collector (实验 e63ec33e I1)。

扫描 ``.map/claude-runtime-home-{host,participant,reviewer}/.claude/projects/.../*.jsonl``
提取 ``type=assistant`` 行的原始 usage 块；输出 ``RawUsageEvent`` 流供
Layer 2 映射器（layer2_mapper.py）按 ``version + field_name`` dict 归一化。

设计原则（plan §A1）：
- **不假设字段名**：采集器只 yield 原始 dict；映射器单独管 schema 版本
- **损坏行 warn 不崩**：json.JSONDecodeError / 缺字段 → warn + 跳过
- **append-only 流式读取**：不一次性 load 全文件；waker 写入不阻塞
- **单主机战役**：跨主机不做（plan §A7 边界）
- **persona 从路径派生**：runtime_home 目录名 = persona（host / participant / reviewer）

数据样本（spike_note.md:26-48 实测）：
    {"type": "assistant", "message": {"usage": {input_tokens, output_tokens,
     cache_creation_input_tokens, cache_read_input_tokens}, ...}, "uuid": ...,
     "timestamp": "...", "sessionId": "...", "version": "2.1.191", ...}
"""
from __future__ import annotations

import json
import logging
import warnings
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_log = logging.getLogger("cli.cost_ledger.layer1_collector")

# persona -> runtime home 子目录名（固定三种，与 cli/simple_waker.py 一致）
RUNTIME_HOME_PERSONAS: tuple[str, ...] = ("host", "participant", "reviewer")

# project_dir 是 runtime home 下 .claude/projects/<cwd-hash> 的最后一段
# cwd 路径里的 '/' 替换为 '-'（实测 spike_note.md 验证）
# 例：/home/AI02/.../multi_agents_platform → -home-AI02-...-multi_agents_platform
PROJECT_DIR_SUFFIX = "-home-AI02-Documents-quantaeye-multi-agents-platform"


@dataclass(frozen=True)
class RawUsageEvent:
    """采集器输出单元；含完整 raw_payload + 派生元数据。

    ``persona`` 从文件路径 prefix 解析（runtime_home-{persona}/.claude/...）；
    ``session_id`` 从文件名（去掉 .jsonl 后缀）；
    ``ts`` 取顶层 ``timestamp`` 字段（ISO 8601）；
    ``version`` 取顶层 ``version``（Claude Agent SDK 版本，映射器按它查表）。
    """

    persona: str
    session_id: str
    ts: str
    version: str
    source_file: str
    raw_payload: dict[str, Any] = field(default_factory=dict)


def _runtime_home_for(project_root: Path, persona: str) -> Path:
    """Return ``<project_root>/.map/claude-runtime-home-<persona>``."""
    return project_root / ".map" / f"claude-runtime-home-{persona}"


def _project_jsonl_paths(project_root: Path) -> Iterator[tuple[str, Path]]:
    """Yield ``(persona, jsonl_path)`` for every session file under 3 runtime homes.

    路径契约：
      ``<project_root>/.map/claude-runtime-home-<persona>/.claude/projects/<PROJECT_DIR_SUFFIX>/<session_id>.jsonl``

    与 simple-waker / claude-runtime SDK 默认 layout 对齐。
    """
    for persona in RUNTIME_HOME_PERSONAS:
        runtime_home = _runtime_home_for(project_root, persona)
        if not runtime_home.is_dir():
            continue
        project_dir = runtime_home / ".claude" / "projects" / PROJECT_DIR_SUFFIX
        if not project_dir.is_dir():
            continue
        for jsonl_path in sorted(project_dir.glob("*.jsonl")):
            yield persona, jsonl_path


def _parse_line(line: str, source_file: str) -> RawUsageEvent | None:
    """Parse a single JSONL line into ``RawUsageEvent``; corrupt lines return None.

    仅取 ``type=assistant`` 行（其他类型含 user / queue-operation 等无 usage）；
    ``persona`` 由 caller 通过文件路径注入（_project_jsonl_paths）。
    """
    line = line.strip()
    if not line:
        return None
    try:
        payload = json.loads(line)
    except json.JSONDecodeError as exc:
        warnings.warn(
            f"layer1_collector: corrupt JSONL line skipped ({source_file}: {exc.msg})",
            RuntimeWarning,
            stacklevel=2,
        )
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("type") != "assistant":
        return None
    return RawUsageEvent(
        persona="",  # 由 caller 注入
        session_id=str(payload.get("sessionId") or ""),
        ts=str(payload.get("timestamp") or ""),
        version=str(payload.get("version") or ""),
        source_file=source_file,
        raw_payload=payload,
    )


def scan_session_jsonl(jsonl_path: Path, *, persona: str) -> Iterator[RawUsageEvent]:
    """Stream-read one jsonl file, yielding ``RawUsageEvent`` per assistant row.

    损坏行：warn 但聚合继续（plan §A7）；不抛异常（I1 契约）。
    """
    source = str(jsonl_path)
    try:
        with jsonl_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                event = _parse_line(line, source_file=source)
                if event is None:
                    continue
                # 注入 caller 派生的 persona（路径前缀）
                event = RawUsageEvent(
                    persona=persona,
                    session_id=event.session_id,
                    ts=event.ts,
                    version=event.version,
                    source_file=event.source_file,
                    raw_payload=event.raw_payload,
                )
                yield event
    except OSError as exc:
        warnings.warn(
            f"layer1_collector: failed to read {jsonl_path}: {exc}",
            RuntimeWarning,
            stacklevel=2,
        )


def scan_runtime_homes(project_root: Path) -> Iterator[RawUsageEvent]:
    """Scan all 3 persona runtime homes; yield ``RawUsageEvent`` for every assistant row.

    主入口。CLI ``map experiment show --cost`` / ``map waker costs --by-*``
    通过 lib/cost_ledger/aggregation.py 调用本函数做 raw 扫描。
    """
    for persona, jsonl_path in _project_jsonl_paths(project_root):
        yield from scan_session_jsonl(jsonl_path, persona=persona)


__all__ = [
    "RawUsageEvent",
    "RUNTIME_HOME_PERSONAS",
    "PROJECT_DIR_SUFFIX",
    "scan_runtime_homes",
    "scan_session_jsonl",
    "_runtime_home_for",
    "_project_jsonl_paths",
    "_parse_line",
]
