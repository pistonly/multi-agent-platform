# I5 — commit + complete + release

## Git 提交

```
57d76e5 map exp 715202a3: waker 巡检视图 — cycle 累加 + map waker status CLI + 15 case 测试 (I1-I5)
7 files changed, 843 insertions(+)
create mode 100644 cli/commands/waker_status.py
create mode 100644 cli/waker_status_view.py
create mode 100644 tests/test_waker_status.py
```

白名单严格遵守 plan A7：`^cli/` + `^tests/`，无意外漂移。

## 实测核验

| 命令 | 结果 |
|------|------|
| `ruff check` (7 个改动文件) | All checks passed! |
| `pytest tests/test_waker_status.py -q` | 15 passed |
| `pytest tests/test_simple_waker.py + cli/test_compat.py + cli/test_dry_run_write_commands.py -q` | 80 passed |
| `pytest tests/ -q` (full suite) | 1818 passed / 2 skipped / 359 deselected, **0 failed** |
| `map waker status` | 渲染 3 行 + WARN + HINT（state=dead 反映旧 state 缺新字段；符合预期）|
| `map waker status --json` | 结构化 JSON 输出 |

基线对比：1803 → 1818（+15，仅 test_waker_status.py 新增；0 failed；只增不减）。

## Acceptance A1-A8 全量核验

- **A1 单源**：cli/simple_waker.py 复用既有 `.map/simple-waker-state-{persona}.json`（I1 设计收敛）+ atomic write（既有 `_save_state_if_needed` 路径含 tmp + rename）；视图命令只读 JSON 不写 ✓
- **A2 stale 三档**：cli/waker_status_view.py `compute_waker_state` 实现 live/stale/dead + busy 卡死升级（>5min 自动 stale）；4 case 覆盖 ✓
- **A3 字段最小集 10**：`render_waker_status_table` 输出 10 列；`test_render_table_has_exactly_10_columns` 守卫 ✓
- **A4 视图只读**：`collect_waker_status` 仅 read + stdout；`test_collect_waker_status_is_read_only` 守卫 mtime + 文件集合不变 ✓
- **A5 重启归档**：pid 变化 → 写 `.stale.<ts>.json` sidecar + 重置 cycles/runtime session 保留；2 case 覆盖（含 pid 同反例）✓
- **A6 ≥5 case**：15 case 远超下限 ✓
- **A7 ruff 0 + pytest 全绿**：实测见上 ✓
- **A8 边界**：未引入新 server 端点/DB 字段；sync_runtime_skills 边界外；与 T1-T4 链路无冲突 ✓

## 后续动作

1. `experiment complete` → phase=running → result_review
2. reviewer 端审：`map --persona reviewer experiment status` + 提交评审
3. reviewer accept 后 → done
4. release lock + 话题 close（议题 7f0e8de7 等 reviewer round2 ack 后 close）
5. 监督者重启 waker（验收通过后由监督者重启 simple-waker，I2 字段自动开始累加）

## 实验状态

phase=running / log_count=3（I2/I3/I4 已写）
下一步：`map experiment complete --id 715202a3 --metadata ...`
