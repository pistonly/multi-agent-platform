"""MAP runtime token 消耗探针（只读，不改任何状态）。

为什么需要它
------------
``map experiment show --cost`` 走 ``cli/cost_ledger`` 的版本键控归一化
（``VERSION_FIELD_MAP``），未登记的 SDK version 会落入 unknown 分支 →
4 个 token 字段全为 None，被聚合层当作 0 静默吞掉。2026-09-21 实测：
SDK ``2.1.277`` 的 777 行 / 68.5M input token 未被计入，账本口径 243M
vs 原始口径 311M，**漏计 22%**。

本脚本直读 ``.map/claude-runtime-home-<persona>/.claude/projects/**/*.jsonl``
的原始 ``message.usage`` 字段，不经归一化，用于：

1. 得到真实消耗基线（不被未知 version 吞噬）
2. 暴露 prompt caching 命中率（cache_read / 总输入）
3. 定位上下文膨胀（单 session 轮次、峰值、均值、压缩次数）

用法
----
    python docs/probes/token-cost-audit.py            # 默认扫当前目录
    python docs/probes/token-cost-audit.py --root /path/to/project
    python docs/probes/token-cost-audit.py --curve    # 追加最大 session 的膨胀曲线

输出纯文本表格，无副作用。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import statistics
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

PERSONAS = ("host", "participant", "reviewer")


@dataclass
class SessionStat:
    """单个 jsonl（= 一个 runtime session）的 token 画像。"""

    persona: str
    session_id: str
    turns: int
    first: int
    peak: int
    last: int
    mean: int
    total: int
    compactions: int


def iter_assistant_rows(root: Path) -> list[tuple[str, str, dict]]:
    """Yield ``(persona, session_id, usage_dict)`` for every assistant row."""
    rows: list[tuple[str, str, dict]] = []
    for path in sorted(glob.glob(str(root / ".map" / "claude-runtime-home-*" / ".claude" / "projects" / "*" / "*.jsonl"))):
        persona = _persona_of(path)
        session_id = os.path.basename(path)[:8]
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if payload.get("type") != "assistant":
                    continue
                usage = (payload.get("message") or {}).get("usage") or {}
                rows.append((persona, session_id, usage))
    return rows


def _persona_of(path: str) -> str:
    for p in PERSONAS:
        if f"claude-runtime-home-{p}" in path:
            return p
    return "unknown"


def collect(root: Path) -> tuple[list[SessionStat], Counter, Counter]:
    """Aggregate per-session stats plus global token totals and version counts."""
    per_session: dict[tuple[str, str], list[int]] = {}
    totals: Counter = Counter()
    versions: Counter = Counter()
    for persona, session_id, usage in iter_assistant_rows(root):
        vals = per_session.setdefault((persona, session_id), [])
        inp = int(usage.get("input_tokens") or 0)
        vals.append(inp)
        totals["input"] += inp
        totals["output"] += int(usage.get("output_tokens") or 0)
        totals["cache_read"] += int(usage.get("cache_read_input_tokens") or 0)
        totals["cache_creation"] += int(usage.get("cache_creation_input_tokens") or 0)
        totals["rows"] += 1
    stats = [
        SessionStat(
            persona=p,
            session_id=s,
            turns=len(v),
            first=v[0],
            peak=max(v),
            last=v[-1],
            mean=int(statistics.mean(v)),
            total=sum(v),
            compactions=sum(1 for i in range(1, len(v)) if v[i - 1] > 0 and v[i] < v[i - 1] * 0.6),
        )
        for (p, s), v in per_session.items()
    ]
    stats.sort(key=lambda s: -s.total)
    return stats, totals, versions


def report(stats: list[SessionStat], totals: Counter) -> None:
    """Print the persona/session breakdown plus cache-hit diagnostics."""
    print(f"assistant 行数: {totals['rows']:,}\n")
    print(f"{'persona':<12}{'session':<10}{'轮次':>6}{'首轮':>9}{'峰值':>10}{'均值':>9}{'累计M':>9}{'压缩':>6}")
    for s in stats:
        print(
            f"{s.persona:<12}{s.session_id:<10}{s.turns:>6}{s.first:>9,}{s.peak:>10,}"
            f"{s.mean:>9,}{s.total / 1e6:>9.1f}{s.compactions:>6}"
        )

    total_in = totals["input"] + totals["cache_read"] + totals["cache_creation"]
    print("\n=== 全局 ===")
    print(f"input          : {totals['input']:,}")
    print(f"output         : {totals['output']:,}")
    print(f"cache_read     : {totals['cache_read']:,}")
    print(f"cache_creation : {totals['cache_creation']:,}")
    if total_in:
        hit = totals["cache_read"] / total_in * 100
        print(f"\n缓存命中率: {hit:.1f}%  (总输入 {total_in:,})")
        if hit < 5:
            print("  !! 缓存几乎未命中——每轮输入都按全价计费。")
            print("     常见根因：ANTHROPIC_BASE_URL 指向的中转网关未转发 cache_control 断点。")
    if stats:
        worst = stats[0]
        print(f"\n最大 session: {worst.persona}/{worst.session_id} "
              f"{worst.turns} 轮，均值 {worst.mean:,}，累计 {worst.total / 1e6:.1f}M")
        share = worst.total / max(sum(s.total for s in stats), 1) * 100
        print(f"  占总消耗 {share:.0f}%——单点优化收益集中在这里。")


def main() -> None:
    """CLI entrypoint."""
    ap = argparse.ArgumentParser(description="MAP runtime token 消耗探针")
    ap.add_argument("--root", default=".", help="项目根目录（含 .map/）")
    ap.add_argument("--curve", action="store_true", help="追加最大 session 的逐轮膨胀曲线数据")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    stats, totals, _ = collect(root)
    report(stats, totals)

    if args.curve and stats:
        worst = stats[0]
        pattern = str(root / ".map" / f"claude-runtime-home-{worst.persona}" / ".claude" / "projects" / "*" / "*.jsonl")
        seq: list[int] = []
        for path in glob.glob(pattern):
            if not os.path.basename(path).startswith(worst.session_id):
                continue
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if payload.get("type") == "assistant":
                        seq.append(int((payload.get("message") or {}).get("usage", {}).get("input_tokens") or 0))
        step = max(1, len(seq) // 70)
        pts = [[i, seq[i]] for i in range(0, len(seq), step)]
        print("\n=== 膨胀曲线（抽样，可粘进 Chart.js）===")
        print(json.dumps(pts))


if __name__ == "__main__":
    main()
