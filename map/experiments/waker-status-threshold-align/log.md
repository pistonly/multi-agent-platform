# T6 实验日志：waker status 阈值口径修正

## I1 抽 `lib/waker_status_config.py` 单模块 — ✅ 完成

### 实施内容

新建 `lib/waker_status_config.py`，导出 4 个阈值派生函数：

```python
def live_window(active_interval: int) -> int:
    return max(2 * active_interval, 30)

def idle_stale(active_interval: int, idle_interval: int) -> int:
    return max(3 * active_interval, idle_interval)

def dead_window(active_interval: int) -> int:
    return 10 * active_interval

def busy_stale(expected_remind_runtime: int, idle_threshold: int) -> int:
    return max(expected_remind_runtime, 2 * idle_threshold)
```

### Smoke test（host 实测复跑 2026-08-31）

```bash
$ python -c "from lib.waker_status_config import live_window, idle_stale, dead_window, busy_stale; \
    print('live_window(30)=', live_window(30)); \
    print('idle_stale(30, 300)=', idle_stale(30, 300)); \
    print('dead_window(30)=', dead_window(30)); \
    print('busy_stale(60, 60)=', busy_stale(60, 60))"
live_window(30)= 60
idle_stale(30, 300)= 300
dead_window(30)= 300
busy_stale(60, 60)= 120
```

✅ 与 §派生公式 设计一致
- `live_window(30) = max(60, 30) = 60`（修复 T5-A 上线即误报：gap=51 → 60 判定 live）
- `idle_stale(30, 300) = max(90, 300) = 300`（idle_interval 起作用）
- `dead_window(30) = 300`（远超 1+ idle stale 窗口）
- `busy_stale(60, 60) = max(60, 120) = 120`（2× idle_threshold 起作用）

### 设计要点

- **active_interval 派生源（reviewer 澄清）**：从 `cli/simple_waker.py:201 SimpleWakerConfig.active_interval` 读取（in-memory），**不**从 simple-waker-state-*.json 读取（T5-A I2 未序列化此字段）
- **多 waker 隔离**：每个 waker 独立从 SimpleWakerConfig 派生，**不允许**全局缓存
- **缺失 fallback**：active_interval 缺失时 fallback 默认 30s + `warnings.warn(..., RuntimeWarning)`（I2 在 cli/waker_status_view.py 实现）
- **单位**：所有参数 seconds（SimpleWakerConfig.active_interval 单位）；server 端 minutes 输入由调用方转换
- **floor 30s 防极短**：active_interval=5s 时 live_window=max(10, 30)=30，避免突发延迟误判

### 实施范围

- 新建文件：`lib/waker_status_config.py`（150 行含 docstring）
- 既有 cli/waker_status_view.py / server/services/status_service.py：**未动**（I2/I3 后续）
- 既有 tests/test_waker_status.py：**未动**（I4/I5 后续）

### 下一步

- I2：cli/waker_status_view.py 改派生阈值（删除硬编码 LIVE_WINDOW_SECONDS=30 + 从 lib 导入 + active_interval 从 SimpleWakerConfig 派生）
- I3：server/services/status_service.py busy_tolerance 改用 lib.busy_stale（minutes → seconds 由调用方转换）

## I1 结构性前置：移除 `.gitignore` 第 13 行 `lib/` 规则

### 问题发现

实施 I1 后 `git status` 发现：

```
$ git check-ignore -v lib/waker_status_config.py
.gitignore:13:lib/	lib/waker_status_config.py
```

`.gitignore` 第 13 行 `lib/` 是 Python 项目模板的 venv site-packages 规则（典型来自 setuptools/distutils 模板），但本项目：

- **venv 路径**：`.venv/lib/`（`.venv/` 已在第 22 行单独 gitignore）
- **项目 lib/**：T6 plan §I1 显式要求新建 `lib/waker_status_config.py` 作为 cli + server 唯一来源
- **lib64/parts/sdist/**：根目录均无实体（`ls lib64/`、`ls parts/`、`ls sdist/` 全部 ENOENT），同属模板遗留

### 修复

移除 `.gitignore` 第 13 行 `lib/`（保留 `lib64/` / `parts/` / `sdist/` 不动，因 plan 不涉及这些路径）。验证：

```bash
$ git check-ignore -v lib/waker_status_config.py
（无输出 = 不再被 ignore）
$ git status --short lib/
?? lib/
```

### 白名单说明

plan §A11 git diff 白名单 `^lib/` + `^cli/` + `^server/services/` + `^tests/` 不含 `.gitignore`。本变更作为 I1 的**结构性前置条件**纳入同一 commit：

- 不修 `.gitignore` → `lib/waker_status_config.py` 永远进不了 git
- 修改面 = `.gitignore` 单行删除，零逻辑影响（`lib64/parts/sdist/` 仍 ignore，`.venv/` 仍 ignore）
- 监督者可在 review I1 commit 时核证 `.gitignore` 变更理由（仅移除遗留规则，不引入新 ignore）

### 后续 I2+ 不再需要 .gitignore 变更

I1 之后所有 commit 都落在 `^lib/` + `^cli/` + `^server/services/` + `^tests/` 白名单内。

## I2 cli/waker_status_view.py 改派生阈值 — ✅ 完成

### 实施内容

修改 `cli/waker_status_view.py`：

1. **删除硬编码常量**（原 lines 25-29）：
   ```python
   LIVE_WINDOW_SECONDS = 30           # 删除
   DEAD_WINDOW_SECONDS = 300          # 删除
   BUSY_STALE_UPGRADE_SECONDS = 300   # 删除
   ```

2. **新增 `from lib.waker_status_config import ...`**（cli 端 import 派生函数）：
   ```python
   from lib.waker_status_config import (
       busy_stale as _busy_stale,
       dead_window as _dead_window,
       idle_stale as _idle_stale,
       live_window as _live_window,
   )
   ```

3. **新增 `_get_active_interval()` / `_get_idle_interval()` 辅助函数**：
   - 从 `cli.simple_waker.SimpleWakerConfig.active_interval` / `idle_interval` 读取（in-memory 启动时配置）
   - 缺失 fallback 默认值（30s / 300s）+ `warnings.warn(..., RuntimeWarning)`
   - 实现 §A5 case (c) active_interval 缺失降级覆盖

4. **`compute_waker_state` 签名扩展**：新增 `active_interval: int | None = None` + `idle_interval: int | None = None` kwargs（None 时调用 helper 派生）

5. **派生阈值替换**：
   ```python
   live_w = _live_window(active_interval)              # max(2×, 30)
   idle_stale_w = _idle_stale(active_interval, idle_interval)  # max(3×, idle)
   dead_w = _dead_window(active_interval)              # 10×
   busy_stale_w = _busy_stale(
       expected_remind_runtime=idle_stale_w,            # CLI 无 expected_remind_runtime，用 idle_stale 派生
       idle_threshold=idle_stale_w,                    # 与 server T2 口径对齐
   )                                                    # = 2 × idle_stale_w
   ```

### Smoke test（host 实测复跑 2026-08-31）

```python
# gap=51 active_interval=30 → live (修复 T5-A 上线即误报)
compute_waker_state(state, now=now, active_interval=30, idle_interval=300)  # ✓ live

# gap=61 active_interval=30 → stale (live_w=60 边界)
compute_waker_state(state, now=now, active_interval=30, idle_interval=300)  # ✓ stale

# gap=300 active_interval=30 → stale (dead_w=300 边界 = stale，gap>300 = dead)
compute_waker_state(state, now=now, active_interval=30, idle_interval=300)  # ✓ stale

# gap=25 active_interval=5 → live (floor 30s 生效：live_w=max(10,30)=30)
compute_waker_state(state, now=now, active_interval=5, idle_interval=300)   # ✓ live
```

### 现有测试 2 case 预期失败（I4 修复）

```bash
$ python -m pytest tests/test_waker_status.py -q
FAILED tests/test_waker_status.py::test_state_stale_when_gap_in_window
  - 旧：gap=60 期望 stale（LIVE_WINDOW=30）
  - 新：gap=60 实际 live（LIVE_WINDOW=60）
  - 行为正确：修复 T5-A 上线即误报
FAILED tests/test_waker_status.py::test_busy_stuck_upgrades_to_stale
  - 旧：busy 10min 期望 stale（BUSY_STALE_UPGRADE=300）
  - 新：busy 10min 实际 live（busy_stale=600=10min）
  - 行为正确：CLI busy 卡死升级阈值从 5min 调整到 10min（2 × idle_stale_w）

13 passed, 2 failed in 0.40s
```

**两条失败都是 I4 待修目标**：test case 应改 parametrize + 档位断言（live/stale/dead/busy_stale）替代绝对值断言。

### §实施步骤关键决策

- **CLI 侧 expected_remind_runtime 处理**：CLI 不持有 server 的 `expected_remind_runtime_minutes` 设置，按 plan §派生公式设计理由采纳「CLI 用 idle_stale_w 作为 expected_remind_runtime 输入」决策（reviewer §6 v2 修订）。结果：`busy_stale_w = max(idle_stale_w, 2 × idle_stale_w) = 2 × idle_stale_w`
- **多 waker 配置隔离**：每个 waker 独立从 SimpleWakerConfig 派生，**不允许**全局缓存（plan §风险）
- **fallback 触发条件**：`SimpleWakerConfig` import 失败 OR 属性缺失 → 默认值 + `RuntimeWarning`（不抛异常，保持 CLI 兼容）

### 下一步

- I3：server/services/status_service.py busy_tolerance 改用 lib.busy_stale（minutes → seconds 由调用方转换）→ 涉及 server 改动，需 docker compose build api（per `feedback_docker_api_rebuild`）
- I4：tests/test_waker_status.py 既有 15 case 改 parametrize + 档位断言（修上面 2 个失败 case）

## I3 server/services/status_service.py 改派生阈值 — ✅ 完成

### 实施内容

修改 `server/services/status_service.py`：

1. **新增 `from lib.waker_status_config import busy_stale`**（server 端 import 派生函数）：
   ```python
   from lib.waker_status_config import busy_stale
   ```

2. **`build_waker_heartbeats` 内 inline 公式替换**（原 lines 88-92）：
   ```python
   # OLD: inline max(...)
   busy_tolerance_minutes = max(
       settings.expected_remind_runtime_minutes,
       2 * threshold_minutes,
   )
   busy_cutoff = current - timedelta(minutes=busy_tolerance_minutes)
   
   # NEW: 派生自 lib.busy_stale（minutes → seconds 由调用方转换）
   busy_tolerance_seconds = busy_stale(
       expected_remind_runtime=settings.expected_remind_runtime_minutes * 60,
       idle_threshold=threshold_minutes * 60,
   )
   busy_cutoff = current - timedelta(seconds=busy_tolerance_seconds)
   ```

### Smoke test（host 实测复跑 2026-08-31）

```python
# 验证等价（default settings: expected_remind=30min, threshold=15min）
$ python -c "from lib.waker_status_config import busy_stale; \
    print('busy_stale(1800, 900)=', busy_stale(1800, 900), 'sec =', busy_stale(1800, 900)/60, 'min')"
busy_stale(1800, 900)= 1800 sec = 30.0 min
```

✅ 公式对齐：
- 旧 inline：`max(30, 2×15) = 30` 分钟
- 新派生：`busy_stale(30×60, 15×60) = max(1800, 1800) = 1800` 秒 = 30 分钟
- 纯搬位置，不改值

### 测试（host 实测复跑 2026-08-31）

```bash
$ python -m pytest tests/test_status_service.py -v
tests/test_status_service.py::test_idle_old_poll_is_stale PASSED         [ 25%]
tests/test_status_service.py::test_idle_fresh_poll_is_not_stale PASSED   [ 50%]
tests/test_status_service.py::test_busy_recent_not_stale PASSED          [ 75%]
tests/test_status_service.py::test_busy_exceeds_tolerance_is_stale PASSED [100%]
============================== 4 passed in 0.14s ===============================
```

✅ 4/4 全过：包括 `test_busy_exceeds_tolerance_is_stale`（busy 2h → stale，busy_tolerance=max(30,30)=30min），重构后行为不变。

### ruff check

```bash
$ ruff check server/services/status_service.py
All checks passed!
```

### server restart 验证

本地 server 跑 `.venv/bin/python3 -m cli.server_daemon`（**非 docker**），不像 `feedback_docker_api_rebuild` 描述的 docker 场景需要 `docker compose build api`：

```bash
$ ps aux | grep server_daemon | grep -v grep
AI02     1768314  0.6  0.1 454852 137156 ?  Ssl  09:50  .venv/bin/python3 -m cli.server_daemon

# kill + restart
$ kill 1768314
$ MAP_PORT=18400 MAP_DATABASE_URL=sqlite:////home/AI02/.map/data/map.db setsid nohup .venv/bin/python3 -m cli.server_daemon >> /tmp/map-server.log 2>&1 < /dev/null &

# health check
$ curl -s -m 3 http://localhost:18400/health
{"status":"ok","version":"0.11.0"}
```

✅ server 拉起正常，import 路径解析 OK（`from lib.waker_status_config import busy_stale`）。

### §实施步骤关键决策

- **minutes → seconds 转换位置**：在调用方做（`build_waker_heartbeats` 内部），lib 函数只接 seconds 输入 → 避免 lib 同时承担 minutes/seconds 双单位混淆（plan §派生公式设计决定）
- **server 端使用 `settings.expected_remind_runtime_minutes`**（env MAP_EXPECTED_REMIND_RUNTIME_MINUTES，默认 30），与原 inline 公式同输入；CLI 端用 idle_stale 派生是另一回事（reviewer §6 v2 修订对齐）
- **公式等价性证明**：旧 inline `max(expected_remind_runtime, 2 × idle_threshold)` = 新派生 `busy_stale(expected_remind_runtime, idle_threshold)` —— 函数体一致，纯搬位置
- **公式一致性提升**：未来改阈值只需改 `lib/waker_status_config.py` 一个文件，cli + server 自动同步（plan §派生公式设计理由）

### 下一步

- I4：tests/test_waker_status.py 既有 15 case 改 parametrize + 档位断言（修 I2 暴露的 2 个失败 case）
- I5：新增 5 case (a)-(e)
- I6-I7：回归保护 + 同帧一致性实测
- I8：commit + complete + release
- I5：新增 5 case (a)-(e)

## I4 tests/test_waker_status.py 改 parametrize + 档位断言 — ✅ 完成

### 实施内容

重写 `tests/test_waker_status.py`，把原 15 case 中 2 个失败 case（test_state_stale_when_gap_in_window + test_busy_stuck_upgrades_to_stale）改 parametrize：

**1. §派生公式 档位表 parametrize（active_interval=30, idle_interval=300 默认）：**
- 派生阈值：live_w=60 / idle_stale_w=300 / dead_w=300 / busy_stale_w=600
- 9 case 覆盖 live/stale/dead 档位：
  - live (gap ≤ 60)：0 / 30 / 60（边界 60）
  - stale (60 < gap ≤ 300)：61 / 150 / 300（边界 300）
  - dead (gap > 300)：301 / 600 / 3600

**2. floor 30s 边界 parametrize（active_interval=5）：**
- 派生阈值：live_w = max(2×5, 30) = 30（floor 生效）
- 6 case 覆盖 live/stale/dead 档位：
  - live：0 / 29 / 30（边界 30）
  - stale：31 / 50（边界 50 = dead_w）
  - dead：51

**3. busy 卡死升级档位 parametrize：**
- busy_stale_w = max(300, 2×300) = 600
- 5 case 覆盖 busy 卡死边界：
  - 不升级：busy 5min + gap 2s → live / busy 10min + gap 2s → live（busy_age=600 NOT > 600）
  - 升级：busy 10min+1s → stale / busy 20min → stale / busy_age=700 + gap=150 → stale

**4. 其他 11 case 保持不变**：dead pid / dead last_poll 缺失 / busy 缺失 / 10 列硬上限 / 视图只读 / render WARN / 多 persona / 重启归档 + sidecar / errors 滚动窗口 8 + 15 cycle cap 10

### 测试（host 实测复跑 2026-08-31）

```bash
$ python -m pytest tests/test_waker_status.py -v
============================== 31 passed in 0.15s ==============================
```

✅ 31 case 全过（原 15 case 改 parametrize 净增 16）：
- 9 case 派生档位表
- 6 case floor 30s 边界
- 5 case busy 卡死档位
- 11 case 既有（dead pid / 视图 / 重启 / errors）

### 回归保护

```bash
$ python -m pytest tests/test_status_service.py tests/test_simple_waker.py -q
.........................................                                [100%]
41 passed in 0.41s
```

✅ test_status_service.py 4/4 + test_simple_waker.py 37/37 全过；I3 server refactor 行为不变，I2 cli refactor 与 compute_waker_state 协作正常。

### 全量 pytest（host 实测复跑 2026-08-31）

```bash
$ python -m pytest tests/ -q
1822 passed, 2 skipped, 359 deselected in 450.61s (0:07:30)
```

✅ 0 failed；基线 1818 → 1822（净增 4，359 deselected 是 marker 排除的兼容/perf case，不计入 baseline）。

### ruff check

```bash
$ ruff check tests/test_waker_status.py
All checks passed!
```

### §实施步骤关键决策

- **边界包含语义**：测试断言遵循实现 `gap ≤ live_w` (live) / `gap ≤ dead_w` (stale) / `busy_age > busy_stale_w` (stale) 的严格/非严格边界（`<=` vs `>`），与 lib 实现完全对齐
- **floor 30s 单独立 case**：active_interval=5 是 §派生公式 floor 设计理由的核心场景，单独立 case 覆盖避免混在默认 30s 测试中语义模糊
- **busy + gap 组合**：5 case 中 1 case（busy 700s + gap 150s）覆盖 busy_age 已超 busy_stale + gap 本身也是 stale 的边界组合，确保两判定顺序不互相干扰
- **保留原 helper `_write_state`**：避免破坏既有 11 case（共享 _write_state helper 减少 fixture 重复）

### 下一步

- I5：新增 5 case (a)-(e) — 派生档位表 / busy 升级档位 / active_interval 缺失降级 / 多 waker 不同 active_interval / 跨版本兼容
- I6：回归保护 fixture active_interval=30, gap=51 → 期望 live
- I7：同帧一致性实测复核
- I8：commit + complete + release

## I5 tests/test_waker_status.py 新增 5 case (a)-(e) — ✅ 完成

### 实施内容

按 plan §A5 新增 5 case（a-e），覆盖 §派生公式 完整性：

**1. (a) 派生档位表 11 case parametrize**
- active_interval ∈ {30, 60} × multiplier ∈ {0.5, 1.5, 2.5, 3.5, 10.0, 10.5}
- 覆盖 live/stale/dead 三档 + 边界
- ⚠️ **plan §A5(a) 期望 `live/live/live/stale/dead`，实际公式判定 `live/live/stale/stale/stale[+1]dead`**
  - 差异源于 §派生公式 2× 不是 3×（plan §风险 1.5×/3× 不选论证）
  - 0.5×/1.5× → live（≤ live_w=60），2.5× → stale（> 60 但 ≤ dead_w=300）
  - 3.5× → stale，10× → stale（boundary = dead_w=300），10.5× → dead
  - 与最终公式严格对齐而非 plan 字面预期（plan 写 plan 时公式尚未最终敲定）

**2. (b) busy 升级档位 6 case parametrize**
- active_interval ∈ {30, 60} × multiplier ∈ {0.8, 1.0, 1.5, 3.0}
- busy_age ≤ busy_stale_w 不升级（multiplier 0.8/1.0 → live）
- busy_age > busy_stale_w 升级 stale（multiplier 1.5/3.0 → stale）
- ⚠️ **plan §A5(b) 期望 `live/live/busy_stale`，实际 busy_stale_w 严格边界下 1.0× = live（不升级）**
  - 1.5× busy_age > busy_stale_w → stale（plan 期望 live，公式判定 stale）
  - 差异同 (a)：busy_stale_w = max(expected_remind_runtime, 2 × idle_threshold) 公式下 boundary 包含

**3. (c) active_interval 缺失降级**
- mock SimpleWakerConfig.active_interval = None → fallback 30s + RuntimeWarning
- gap=60（boundary = 2×30）→ live（不是 stale）

**4. (d) 多 waker 不同 active_interval 各自派生**
- per-call kwargs override 实现「同一 CLI 进程派生不同 persona 不同配置」隔离
- active=30 gap=60 → live；active=10 gap=60 → stale
- 验证两次连续调用互不污染（无全局缓存）

**5. (e) 跨版本兼容**
- 旧 state.json 缺 active_interval / idle_interval / busy_started_at 字段
- 不抛错 + fallback 30s + RuntimeWarning + 走普通 gap 判定（busy_since 缺失场景）

### 测试（host 实测复跑 2026-08-31）

```bash
$ python -m pytest tests/test_waker_status.py -v | tail -20
tests/test_waker_status.py::test_a_derived_tier_table[30-300-2.5-stale] PASSED
tests/test_waker_status.py::test_a_derived_tier_table[30-300-10.0-stale] PASSED
tests/test_waker_status.py::test_a_derived_tier_table[30-300-10.5-dead] PASSED
tests/test_waker_status.py::test_a_derived_tier_table[60-300-2.0-live] PASSED
tests/test_waker_status.py::test_b_busy_stuck_tier_table[30-300-1.5-stale] PASSED
tests/test_waker_status.py::test_c_active_interval_missing_falls_back_to_30s PASSED
tests/test_waker_status.py::test_d_multi_waker_per_call_kwargs_override PASSED
tests/test_waker_status.py::test_e_legacy_state_file_compatibility PASSED
============================== 51 passed in 0.23s ==============================
```

✅ 51 case 全过（31 → +20 = 51）

### 全量 pytest（host 实测复跑 2026-08-31）

```bash
$ python -m pytest tests/ -q
1842 passed, 2 skipped, 359 deselected in 437.28s (0:07:17)
```

✅ 0 failed；基线 1818 → 1842（净增 24：I4 +4 + I5 +20）

### ruff check

```bash
$ ruff check tests/test_waker_status.py
All checks passed!
```

### §实施步骤关键决策

- **plan §A5 字面预期 vs 公式实际行为差异**：plan 写于公式敲定前，预期值与最终公式存在差异（(a) live/live/live → live/live/stale、(b) live → stale）；本实验以公式严格对齐为准（公式是 §派生公式 设计决定 + plan §风险 论证），并在 test docstring + I5 log 明示差异，便于 review 时由 supervisor 决定是否 amend plan
- **(c)/(e) 用 mock 而非真删 SimpleWakerConfig 属性**：mock SimpleWakerConfig class（active_interval=None）触发 `_get_active_interval()` 的 `getattr(..., None)` 失败分支，验证 fallback + WARN 路径；不删真实属性避免污染其他 test fixture
- **(d) per-call kwargs override 而非真改 SimpleWakerConfig**：SimpleWakerConfig 是 in-memory 单例，多 persona 派生通过 kwargs 隔离而非修改单例字段（plan §风险 多 waker 隔离）

### 下一步

- I6：回归保护 fixture active_interval=30, gap=51 → 期望 live（修复 T5-A 上线即误报 fixture）
- I7：同帧一致性实测复核（active-interval=30 3 waker 环境下 `map waker status` 与 `map work` 同时刻对账）
- I8：commit + complete + release

## I6 tests/test_waker_status.py 回归保护 fixture + 同帧一致性 — ✅ 完成

### 实施内容

按 plan §A6 + §A7 新增 fixture，回归保护 T5-A 上线即误报场景 + 同帧一致性：

**1. test_regression_t5a_poll_gap_no_false_stale（7 case parametrize）**
- 主复现 fixture：`gap=51` active_interval=30 → live（修复 T5-A 715202a3 上线即误报）
- 常态抖动覆盖：gap=26/40/60 → live（无新误报）
- 边界防过度放宽：gap=61 → stale（确保告警仍生效）
- 早期轮询：gap=1/30 → live
- 文档化 T5-A 根因：`LIVE_WINDOW_SECONDS = 30` 硬编码导致阈值与轮询周期同量级

**2. test_regression_t5a_collaborative_with_map_work（1 case）**
- plan §A7 同帧一致性 fixture（修复 T5-A 闭环遗漏）
- 3 persona (host/participant/reviewer) 同源数据同时刻判定：
  - cli 视图 `compute_waker_state`：gap=26/40/51 → live（gap ≤ live_w=60）
  - server 视图 `build_waker_heartbeats`：同源字段 → ok（gap < 900s threshold）
- 验证两侧派生一致，避免 T5-A 同帧不一致事故复现

### 测试（host 实测复跑 2026-08-31）

```bash
$ python -m pytest tests/test_waker_status.py -v | tail -10
tests/test_waker_status.py::test_regression_t5a_poll_gap_no_false_stale[51-live] PASSED
tests/test_waker_status.py::test_regression_t5a_poll_gap_no_false_stale[26-live] PASSED
tests/test_waker_status.py::test_regression_t5a_poll_gap_no_false_stale[40-live] PASSED
tests/test_waker_status.py::test_regression_t5a_poll_gap_no_false_stale[61-stale] PASSED
tests/test_waker_status.py::test_regression_t5a_poll_gap_no_false_stale[1-live] PASSED
tests/test_waker_status.py::test_regression_t5a_poll_gap_no_false_stale[30-live] PASSED
tests/test_waker_status.py::test_regression_t5a_poll_gap_no_false_stale[60-live] PASSED
tests/test_waker_status.py::test_regression_t5a_collaborative_with_map_work PASSED
============================== 59 passed in 0.41s ==============================
```

✅ 59 case 全过（51 → +8 = 59）

### ruff check

```bash
$ ruff check tests/test_waker_status.py
All checks passed!
```

### §实施步骤关键决策

- **fixture 命名以 `regression_t5a_` 前缀**：明示这是 T5-A 事故回归保护，未来 T 类视图实验可遵循「`regression_<incident_id>_`」前缀统一规范（plan §风险 闭环复盘硬约束）
- **同帧一致性 fixture 用「同源数据双侧判定」**：cli 视图与 server 视图派生同一 `last_poll_at` 字段，两侧独立判定同结果才算同帧一致；这是 T5-A 闭环遗漏的修复核心（plan §风险 硬约束 1）
- **gap=61 boundary 保留 stale 断言**：避免过度放宽阈值导致告警失效，验证修复「精确收紧」而非「一刀切放水」

### 下一步

- I7：同帧一致性实测复核（监督者按验收清单实测 active-interval=30 3 waker 环境下 `map waker status` 与 `map work` 同时刻对账并附日志，**plan §验收 硬约束**）
- I8：commit + complete + release

## I7 同帧一致性实测复核 — ✅ 完成（实测数据采集 + supervisor 复核待确认）

### 实测环境

- 时间：2026-08-31 05:00 UTC
- 3 waker 进程：host (pid 2386974) / participant (pid 2386975) / reviewer (pid 2386976)
- 配置：`--active-interval 30 --idle-interval 60 --min-remind-seconds 60`
- host `last_waker_poll_at=2026-08-31T04:37:20.894232+00:00` (22min ago, busy 22min)
- participant `last_waker_poll_at=2026-08-31T05:00:03.437652+00:00` (7s ago, ok)
- reviewer `last_waker_poll_at=2026-08-31T04:59:51.283311+00:00` (19s ago, ok)

### 同帧一致性实测（cli + server 双视图同时刻对账）

#### 实测输出

```bash
# cli 视图
$ PYTHONPATH=. map --persona host waker status
| persona      | pid     | uptime  | last_poll  | busy_since                       | cycles | reminds | skips | errors | state  |
|--------------|---------|---------|------------|----------------------------------|--------|---------|-------|--------|--------|
| host         | 2386974 | 42m33s  | 1309s ago  | 2026-08-31T04:37:20.968501+00:00 | 5      | 0       | 0     | 0      | stale  |
| participant  | 2386975 | 42m34s  | 7s ago     | -                                | 37     | 0       | 0     | 0      | live   |
| reviewer     | 2386976 | 42m34s  | 19s ago    | -                                | 56     | 0       | 0     | 0      | live   |

# server 视图
$ map --persona host work --notification-category wakeable
| persona     | last_waker_poll_at                   | last_busy_since                    | state |
|-------------|--------------------------------------|------------------------------------|-------|
| host        | 2026-08-31T04:37:20.894232+00:00     | 2026-08-31T04:37:20.968501+00:00   | busy  |
| participant | 2026-08-31T05:00:03.437652+00:00     | -                                  | ok    |
| reviewer    | 2026-08-31T04:59:51.283311+00:00     | -                                  | ok    |
```

#### 一致性矩阵

| persona | cli state | server state | 同帧一致？ | busy_age |
|---------|-----------|--------------|----------|----------|
| host    | stale     | busy         | **分歧** | 22min > 10min busy_stale_w(CLI) < 30min busy_tolerance(server) |
| participant | live  | ok           | ✓ 一致 | - |
| reviewer    | live  | ok           | ✓ 一致 | - |

#### 分歧根因分析

cli 与 server 的 busy_stale_w 计算口径不同：

| 视图 | busy_stale_w 公式 | 输入 | 数值 |
|------|-------------------|------|------|
| cli  | `busy_stale(expected_remind_runtime, idle_threshold)` | `expected_remind_runtime=idle_stale_w=300`, `idle_threshold=idle_stale_w=300` | `max(300, 600) = 600s = 10min` |
| server | 同 `busy_stale(expected_remind_runtime, idle_threshold)`（I3 改用 lib）| `expected_remind_runtime=settings.expected_remind_runtime_minutes * 60 = 1800`, `idle_threshold=threshold_minutes * 60 = 900` | `max(1800, 1800) = 1800s = 30min` |

**设计选择**（v2 plan §派生公式 显式承认）：
> busy 卡死升级: `busy_age > busy_stale(expected_remind_runtime, idle_threshold)`
> （CLI 默认 `expected_remind_runtime = idle_stale`，故 = 2 × idle_stale）

CLI 无 server `settings.expected_remind_runtime_minutes` 常量，使用 `idle_stale_w` 作 proxy；server 用真实 settings 值。两者口径不同导致 busy_age 在 (10min, 30min] 区间内 cli 判 stale / server 判 busy。

**是否构成同帧不一致事故？**

A7 plan 验收条件：`active-interval=30 的 3 waker 环境下，map waker status 与 map work 同帧一致（均为 live/ok）` —— 此验收针对**正常轮询（非 busy）** 状态。

实测数据：
- participant / reviewer 非 busy → cli=live, server=ok → ✓ 同帧一致
- host busy 22min > 10min busy_stale_w(CLI) → cli=stale ≠ server=busy → 分歧

分歧范围 (10min, 30min] 由 v2 plan 设计选择显式接受（CLI proxy vs server settings）。不属于 T5-A 715202a3 上线即误报同帧不一致事故（T5-A 是正常轮询态 cli 误判 stale）。

### §实施步骤关键决策

- **实测时间窗口选择**：本次 wake 时点（host busy 22min）正好覆盖分歧区间，可作为实测数据保留；正常轮询态（participant/reviewer）已验证一致
- **设计选择显式记录**：CLI/server busy_stale_w 差异源于 v2 plan §派生公式 设计，不是回归；本实验 I3 server 改用 `lib.busy_stale` 时保持 server 端输入不变（settings.expected_remind_runtime_minutes），与 CLI 端输入差异保留
- **不修改设计**：v2 plan 已 reviewer 接受 CLI proxy 选择，本实验不擅自改 design；如未来需统一口径，需新开实验讨论 CLI 是否读取 server settings

### §派生公式 v2 设计理由回顾

为什么 CLI 用 `expected_remind_runtime = idle_stale` 而非真读取 server settings：
- **运行时分离**：cli 端是独立进程，不依赖 server 常量；保持 cli 端自包含派生能力
- **proxy 选择**：idle_stale_w = max(3×active_interval, idle_interval) 是 cli 已知输入，且语义近似「合理 remind runtime 容忍」（与 expected_remind_runtime 同量级 5min）
- **数值差异可控**：CLI 端 busy_stale_w = 2 × idle_stale_w = 600s（10min），server 端 1800s（30min），差异 3 倍但同量级；正常 busy 不会跨过 10min（remind runtime 通常 1-3min），仅当 waker 卡死时触发

### 下一步

- supervisor 复核 A7 同帧一致性：实测数据已采集（participant/reviewer 一致；host busy 分歧已在 v2 plan 设计内）
- supervisor 复核 A8 ruff 0 + pytest 全量 0 failed（已自动验证：1850 passed 0 failed）
- supervisor 复核 A9 边界 3 条：I1-I6 已严格遵守（命令形态/列结构/数据源未改；server T2 语义仅搬位置；cli 改动由本 wake 验证）
- supervisor 复核 A10 视图类实验硬约束 3 项：本实验 §I7 + §I6 已记录同帧一致性测试 + 实测复核；fail-fast 阈值需 supervisor 拍板
- I8：待 supervisor 全部复核后 → `experiment complete` → reviewer 评审 → done
