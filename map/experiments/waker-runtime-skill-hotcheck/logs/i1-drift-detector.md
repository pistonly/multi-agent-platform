# I1+I2 — drift_detector.py（per-skill 缓存 + 立即重同步 + 抖动抑制）

## 改动

新建 `cli/drift_detector.py`，承载 waker runtime skill 热自检的核心数据结构和算法。

### DriftEntry / ResyncResult dataclass

```python
@dataclass(frozen=True)
class DriftEntry:
    skill_relpath: str   # 相对路径（e.g. "map-project-collab/SKILL.md"）
    src_mtime_ns: int
    dst_mtime_ns: int
    src_size: int
    dst_size: int
    hash_mismatch: bool = False  # 仅在 mtime+size 不一致后二次 hash 触发

@dataclass
class ResyncResult:
    ok: bool
    resynced_skills: list[str]
    duration_ms: int
    error: str | None = None
```

### DriftDetector

- `last_seen_mtime: dict[str, tuple[int, int]]`（relpath → (mtime_ns, size)）
- `last_resync_at: dict[str, float]`（relpath → monotonic ts，抑制抖动）

`check_drift(source_root, dest_root)`:
- per-skill 单文件粒度遍历：`<source>/<skill>/SKILL.md` + `<source>/<skill>/references/*.md`
- 首次调用全 scan 填充 `last_seen_mtime`（返回空 drift 列表）
- 后续周期增量：mtime_ns + size 双维度比较，**容差 `mtime_delta_ns <= 1_000_000`**（1ms） + size 严格相等才算一致
- 任一不一致 → hash（sha256）二次确认 → 仍不一致 → 返回 DriftEntry
- 副本缺失/新增 → 返回对应 drift（用 sentinel 处理）

`resync(drift_entries, source_root, dest_root)`:
- 调用 `sync_runtime_skills(source_root, dest_root)` 复用现有机制（rmtree+copytree 原子）
- 记录 `last_resync_at[skill]`；同周期同 skill 抑制（`last_resync_at[skill] + 1cycle > now` 直接 skip）
- 失败捕获 → 返回 `ResyncResult(ok=False, error=str(e))`
- 不抛异常（不让 sync 失败阻断 waker 轮询）

## 验收对应

- A2 per-skill 单文件粒度缓存 ✓
- A3 双维度快检 + hash 二次确认 + 1ms 容差 ✓
- A4 立即重同步 + 抖动抑制 ✓

## 未做

- I5 跨平台 mtime 容差（在 DriftDetector 内部已完成，1_000_000ns 默认值即可覆盖；不引入 env 覆盖）
- I6 启动留痕（I3 改 cli/simple_waker.py:1276）
- I7 周期触发（I4 改 cli/simple_waker.py 主循环）
