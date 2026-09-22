"""CLI 出口记账测量面（实验 4e4206de I7）。

每次 ``map`` 调用在命令出口落一行 JSONL 到 ``<map_dir>/usage/cli-calls.jsonl``，
字段 ``{ts, persona, kind, cmd, output_bytes, exit_code}``，用于量化 CLI 输出
速赢（I1/I3/I4/I6）的实际收益——把此前靠人工 before/after 数字对比的验证，
变成可被单条命令复核的持续运行数据。

落点选 ``.map/usage/``（整目录 gitignore）而非 perf-baselines：后者在 ``.map/``
allow-list 内被 git 追踪、承载基线对比快照；usage 是持续追加的运行数据，放
gitignore 目录免污染 git。

``kind`` 取唤醒上下文经 ``MAP_WAKE_KINDS`` 环境变量传入的 work item kinds
（逗号分隔）；非唤醒调用（人工执行 / 无该 env）记 ``null``。唤醒 backend 是
长生命周期会话、env 在 connect 时固定，per-wake kinds 的运行时侧注入属另一
阶段实验（见实验首条 log 边界），本模块只定义 CLI 侧读取契约。

记账是**尽力而为**的观测面：任何写盘 / 解析失败都不得影响命令本身的输出与退出码
（见 :func:`record_cli_call` 的 best-effort 语义）。
"""

from __future__ import annotations

import json
import os
from collections import OrderedDict
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

USAGE_SUBDIR = "usage"
LEDGER_FILENAME = "cli-calls.jsonl"
KINDS_ENV = "MAP_WAKE_KINDS"


def ledger_path(map_dir: Path) -> Path:
    return map_dir / USAGE_SUBDIR / LEDGER_FILENAME


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_since(value: str) -> datetime:
    """把 ``--since`` 解析成 aware datetime（naive 视为 UTC）。

    解析失败抛 :class:`ValueError`，由调用方转成干净 CLI 错误。同时兼容
    ``Z`` 结尾与记账里写的 ``+00:00`` 两种 ISO 变体。
    """
    raw = value.strip()
    if not raw:
        raise ValueError("--since must not be empty")
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def wake_kind_from_env(environ: dict[str, str] | None = None) -> str | None:
    raw = (environ if environ is not None else os.environ).get(KINDS_ENV, "").strip()
    return raw or None


@dataclass(frozen=True)
class CliCallRecord:
    ts: str
    persona: str | None
    kind: str | None
    cmd: str
    output_bytes: int
    exit_code: int
    session_id: str | None = None  # 当前 runtime session join 键（实验 bccb59ea A3）

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "persona": self.persona,
            "kind": self.kind,
            "cmd": self.cmd,
            "output_bytes": self.output_bytes,
            "exit_code": self.exit_code,
            "session_id": self.session_id,
        }

    @property
    def ts_datetime(self) -> datetime | None:
        try:
            return parse_since(self.ts)
        except ValueError:
            return None


def append_record(map_dir: Path, record: CliCallRecord) -> None:
    """追加一行 JSONL（不做 best-effort 兜底——调用方 :func:`record_cli_call` 负责吞错）。"""
    path = ledger_path(map_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")


def read_records(map_dir: Path) -> Iterator[dict[str, Any]]:
    """逐行读取记账记录，容忍缺文件 / 空行 / 损坏行（损坏行静默跳过）。"""
    path = ledger_path(map_dir)
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    yield obj
    except FileNotFoundError:
        return


@dataclass(frozen=True)
class UsageSummaryRow:
    persona: str
    work_calls: int
    total_output_bytes: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "persona": self.persona,
            "work_calls": self.work_calls,
            "total_output_bytes": self.total_output_bytes,
        }


def _record_persona(obj: dict[str, Any]) -> str:
    persona = obj.get("persona")
    return persona if isinstance(persona, str) and persona else "none"


def summarize(records: list[dict[str, Any]], *, since: datetime | None) -> list[UsageSummaryRow]:
    """按 persona 聚合 ``--since`` 窗口内的调用数与输出字节总量。

    ``since`` 为 None 时不过滤（统计全量）。ts 无法解析的记录在给定 ``since``
    时被排除（无法确认落在窗口内）。persona 缺失归入 ``"none"`` 桶。
    """
    work_calls: OrderedDict[str, int] = OrderedDict()
    total_bytes: OrderedDict[str, int] = OrderedDict()
    for obj in records:
        if since is not None:
            ts_raw = obj.get("ts")
            try:
                ts = parse_since(ts_raw) if isinstance(ts_raw, str) else None
            except ValueError:
                ts = None
            if ts is None or ts < since:
                continue
        persona = _record_persona(obj)
        raw_bytes = obj.get("output_bytes")
        out_bytes = raw_bytes if isinstance(raw_bytes, int) and raw_bytes > 0 else 0
        work_calls[persona] = work_calls.get(persona, 0) + 1
        total_bytes[persona] = total_bytes.get(persona, 0) + out_bytes
    return [
        UsageSummaryRow(persona=p, work_calls=work_calls[p], total_output_bytes=total_bytes[p])
        for p in work_calls
    ]


class CountingStdout:
    """透传并计数字节的 stdout 代理（实验 4e4206de I7）。

    替换 ``sys.stdout`` 后 ``typer.echo`` / ``click.echo`` 在进程内首次 echo 时
    解析到本代理（每条 ``map`` 命令独立进程，swap 早于首次输出），因此能统计命令
    写往 stdout 的字节。其余属性（isatty/fileno/encoding…）透传真 stdout。
    已知局限：模块级创建、缓存了真 stdout 的 rich Console 实例不经此代理计数。
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.bytes_written = 0

    def write(self, data: str) -> int:
        written = self._inner.write(data)
        self.bytes_written += len(data.encode("utf-8"))
        return written

    def writelines(self, lines: Any) -> None:
        for line in lines:
            self.write(line)

    def flush(self) -> None:
        self._inner.flush()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def exit_code_from_exc(exc: BaseException | None) -> int:
    """从正在传播的异常推导进程退出码：无异常→0；SystemExit→其 code；其余→1。"""
    if exc is None:
        return 0
    if isinstance(exc, SystemExit):
        code = exc.code
        if code is None:
            return 0
        if isinstance(code, int):
            return code
        return 1
    return 1


# 全局选项（cli/main.py 的 root callback）中携带取值的项：提取子命令路径时连同
# 其后的取值一起跳过，避免把选项值（如 persona 名）误当成命令 token。
_GLOBAL_VALUE_OPTS = {"--persona", "-p", "--project-root", "--config-root", "--format", "-o"}
_GLOBAL_FLAG_OPTS = {"--json", "--debug", "--version"}


def extract_cmd(argv: list[str]) -> str:
    """从 argv 提取子命令路径（如 ``topic comment`` / ``work``）。

    跳过全局选项（root callback）及其取值/取值式 flag；遇到第一个命令级选项
    （非全局、以 ``-`` 开头）即停止收集——其后的 token 视为该命令的参数/取值而非
    命令路径。这样 ``cmd`` 是子命令路径（如 ``topic comment``）而非掺入 id 的
    噪声，便于按命令族聚合（尽力而为，不追求逐命令精确）。
    """
    positional: list[str] = []
    skip_next = False
    for tok in argv:
        if skip_next:
            skip_next = False
            continue
        if tok in _GLOBAL_VALUE_OPTS:
            skip_next = True
            continue
        if tok in _GLOBAL_FLAG_OPTS:
            continue
        if tok.startswith("-"):
            break  # 进入命令级参数区，命令路径到此为止
        positional.append(tok)
    return " ".join(positional)


# ---------------------------------------------------------------------------
# Runtime session 指针 — 两源对账 join 键（实验 bccb59ea A3）
# ---------------------------------------------------------------------------


def _safe_persona_segment(persona: str | None) -> str | None:
    """persona → 指针文件名安全段；空值 / 路径分隔符 / 目录别名 → None。"""
    if not persona or persona in (".", "..") or "/" in persona or "\\" in persona:
        return None
    return persona


def runtime_session_pointer_path(map_dir: Path, persona: str) -> Path:
    """当前 runtime session 指针落点：``.map/usage/runtime-sessions/<persona>.json``。"""
    return map_dir / "usage" / "runtime-sessions" / f"{persona}.json"


def write_runtime_session_pointer(map_dir: Path, persona: str, session_id: str) -> None:
    """唤醒成功后写当前 runtime session 指针（供 ledger 记账与两源对账 join）。"""
    segment = _safe_persona_segment(persona)
    if segment is None or not session_id:
        return
    path = runtime_session_pointer_path(map_dir, segment)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"session_id": session_id}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def clear_runtime_session_pointer(map_dir: Path, persona: str) -> None:
    """session 重置时删除指针，避免 stale join 键（missing_ok 容忍不存在）。"""
    segment = _safe_persona_segment(persona)
    if segment is None:
        return
    runtime_session_pointer_path(map_dir, segment).unlink(missing_ok=True)


def read_runtime_session_id(map_dir: Path | None, persona: str | None) -> str | None:
    """读取 persona 当前 runtime session id；缺失 / 损坏 / persona 不安全 → None。"""
    segment = _safe_persona_segment(persona)
    if map_dir is None or segment is None:
        return None
    try:
        obj = json.loads(
            runtime_session_pointer_path(map_dir, segment).read_text(encoding="utf-8")
        )
    except Exception:  # noqa: BLE001 — measurement surface must never break the CLI
        return None
    session_id = obj.get("session_id") if isinstance(obj, dict) else None
    return session_id if isinstance(session_id, str) and session_id else None


def record_cli_call(
    *,
    map_dir: Path | None,
    persona: str | None,
    cmd: str,
    output_bytes: int,
    exit_code: int,
    environ: dict[str, str] | None = None,
) -> None:
    """命令出口落一行 JSONL——**尽力而为**，任何异常都不外泄（记账不得影响 CLI）。"""
    if map_dir is None:
        return
    try:
        record = CliCallRecord(
            ts=_now_iso(),
            persona=persona,
            kind=wake_kind_from_env(environ),
            cmd=cmd,
            output_bytes=output_bytes,
            exit_code=exit_code,
            session_id=read_runtime_session_id(map_dir, persona),
        )
        append_record(map_dir, record)
    except Exception:  # noqa: BLE001 — measurement surface must never break the CLI
        pass


def resolve_map_dir() -> Path | None:
    """解析当前 workspace 的 ``.map`` 目录；解析失败（无 .map / bootstrap 前）返回 None。"""
    try:
        from cli.project_context import optional_context

        ctx = optional_context()
        return ctx.map_dir if ctx is not None else None
    except Exception:  # noqa: BLE001 — 记账落点解析失败即跳过，不影响命令
        return None


__all__ = [
    "CountingStdout",
    "CliCallRecord",
    "KINDS_ENV",
    "UsageSummaryRow",
    "append_record",
    "clear_runtime_session_pointer",
    "exit_code_from_exc",
    "extract_cmd",
    "ledger_path",
    "parse_since",
    "read_records",
    "read_runtime_session_id",
    "record_cli_call",
    "resolve_map_dir",
    "runtime_session_pointer_path",
    "summarize",
    "wake_kind_from_env",
    "write_runtime_session_pointer",
]
