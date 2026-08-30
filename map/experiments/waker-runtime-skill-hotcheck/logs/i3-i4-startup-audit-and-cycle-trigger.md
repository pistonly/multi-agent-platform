# I3 + I4 — 启动同步留痕 + 周期自检触发

## 改动

### cli/wake_backend.py — `sync_runtime_skills` 签名扩展

```python
def sync_runtime_skills(
    *, project_root: Path, runtime_home: Path
) -> tuple[list[str], str | None]:
    """... 返回 (synced_skills, skipped_reason)"""
    source_root = project_root / ".cursor" / "skills"
    if not source_root.is_dir():
        return ([], "source_missing")
    ...
    return (synced, None)
```

- `skipped_reason` 仅在 `synced_skills == []` 时为 `"source_missing"`
- `PermissionError` 仍由 `sync_runtime_skills` 上抛，由调用方捕获并派生
  `"permission_denied"` 审计行

### cli/simple_waker.py — 启动 audit helper

`_startup_sync_with_audit(project_root, runtime_home)` 在原 line 1276 调用点替换
原裸 `sync_runtime_skills(...)`。Schema:

```json
{
  "event": "startup_sync",
  "ts": "<iso8601>",
  "skills_count": <int>,
  "synced_skills": ["..."],
  "skipped_reason": null | "source_missing" | "permission_denied" | "disabled"
}
```

- `WAKER_SKILL_SYNC_DISABLED=1` env → 写 `disabled` 审计行后 return
- `PermissionError` → warning level，alert 隐含在 `skipped_reason="permission_denied"`
- 普通 success → info level

### cli/simple_waker.py — 周期触发

`SimpleWakerConfig.drift_check_interval_cycles: int = 30`（CLI flag
`--drift-check-interval-cycles` / env `WAKER_DRIFT_CHECK_INTERVAL_CYCLES`）。
`<=0` = 关闭。

`SimpleWaker._build_drift_detector()` 在 `__init__` 末尾构造，runtime_home
缺失或源 skills 不存在时返回 `None`。

`SimpleWaker._run_drift_check(cycle_index=total.cycles)` 在主循环里每
`total.cycles` 累计一次后调用，间隔才触发。审计事件：

| 触发条件 | event | level | alert |
|---------|-------|-------|-------|
| 漂移为空 | `drift_no_change` | DEBUG | false |
| 漂移非空 + resync OK | `drift_resync` | INFO | false |
| 漂移非空 + resync 失败 | `drift_resync_failed` | WARNING | **true** |
| check_drift 自身抛错 | `drift_check_failed` | WARNING | **true** |

`alert=true` 留给监控系统消费（如 stdout/stderr → journald → alertmanager）。

### cli/orchestrator.py / cli/e2e_collab.py / cli/runtime_chat.py — 调用点兼容

`sync_runtime_skills` 旧调用点为 fire-and-forget（`None` 返回）。改为
`_synced, _skipped = sync_runtime_skills(...)` 显式接住 tuple，避免 mypy/Ruff
I001 + 让调用方未来可消费 audit 字段。

## 验收对应

- A5 启动留痕（JSON schema + 4 类 skipped_reason）✓
- A6 每 N 周期触发 + 失败不阻断主流程 ✓
- A7 立即重同步 + 抖动抑制（I1+I2 已实现，resync 接入主循环）✓

## 未做

- I6 6 case 回归测试（`tests/test_simple_waker_drift_detection.py`）
- I7 commit + complete + release + 源话题 close
