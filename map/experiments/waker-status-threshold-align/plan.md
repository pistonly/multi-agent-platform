---
title: "waker status 阈值口径修正（T6）：阈值派生自轮询配置并向 server T2 口径对齐"
acceptance:
  - "A1 同源实现：抽 `lib/waker_status_config.py` 单模块，cli `waker_status_view.py` 与 server `services/status_service.py` 都 `from lib.waker_status_config import ...`；纯模块复用无 HTTP 无副作用；server 现有常量 refactor 出来（搬位置不改值）"
  - "A2 派生公式：4 阈值常量派生自 state.json active_interval/idle_interval + server status_service 常量——`LIVE_WINDOW_SECONDS = max(2 * active_interval, 30)` / `IDLE_STALE_SECONDS = max(3 * active_interval, idle_interval)` / `DEAD_SECONDS = 10 * active_interval` / `BUSY_STALE_SECONDS = server_busy_tolerance`（max(expected_remind_runtime, 2 * idle_threshold)）"
  - "A3 派生公式设计理由（plan §派生公式 显式记录）：2× 是折中——1.5× 在网络抖动场景下容易误判 stale；3× 会让 stale 窗口过大失去告警意义；floor 30s 防 active_interval 极短场景"
  - "A4 既有 tests/test_waker_status.py 15 case 改 parametrize + 档位断言：参数化接收 active_interval fixture，按派生公式计算期望；断言档位（live/stale/dead/busy_stale）替代绝对值；明确豁免『测试要求不变 vs 阈值必须改』死锁"
  - "A5 新增 5 case (a)-(e)：(a) 派生档位表 gap ∈ {0.5×, 1.5×, 2.5×, 3.5×, 10×} active_interval → live/live/live/stale/dead；(b) busy 升级档位 busy_since ∈ {0.8×, 1.5×, 3.0×} expected_remind_runtime → live/live/busy_stale；(c) active_interval 缺失降级 fallback 30s + WARN；(d) 多 waker 不同 active_interval 各自派生不全局缓存；(e) 跨版本兼容 T5-A I2 之前 state.json 降级 + WARN 后自动恢复"
  - "A6 回归保护 fixture：active_interval=30, gap=51 → 期望 live（不是 stale），确保修复有效"
  - "A7 实测复核：active-interval=30 的 3 waker 环境下，`map waker status` 与 `map work` 同帧一致（均为 live/ok）—— **同帧一致性测试**（T5-A 闭环遗漏点）"
  - "A8 ruff check 0；pytest 全量绿（基线 1818 passed / 2 skipped / 359 deselected，只增不减，0 failed）；git diff 白名单 `^lib/`、`^cli/`、`^server/services/`、`^tests/`"
  - "A9 边界：只修阈值口径与测试，不改 waker status 的命令形态/列结构/数据源；不动 server status_service 的 T2 语义（仅搬常量位置不改值）；涉及 cli/ 改动验收通过后由监督者重启 server 与 waker 生效"
  - "A10 视图类实验 plan §验收 硬约束（写入 SKILL/wake.md 防止 T5-A 闭环遗漏复发）：(1) 同帧一致性测试（与同源命令做『同一时刻对账』）；(2) 真实环境实测复核（监督者按验收清单实测 X 项并附日志）；(3) fail-fast 阈值（实测误报率 > 5% 不算闭环，必须返工）"
evidence_keys:
  - "实测输出:(1) 同帧一致性：active-interval=30 的 3 waker 环境下 map waker status 与 map work 同时刻输出对账；(2) 派生档位表 5 档断言全过；(3) 既有 15 case 改 parametrize 后无回归；(4) active_interval 缺失 → fallback 30s + WARN；(5) 多 waker 不同 active_interval 各自派生；(6) 回归 case active_interval=30 gap=51 → live"
  - "grep 核证:lib/waker_status_config.py 存在且导出 4 阈值常量；cli/waker_status_view.py 从 lib 导入；server/services/status_service.py 从 lib 导入；tests/test_waker_status.py 含 15 case parametrize + 5 case (a)-(e) + 1 regression"
  - "pytest_summary:21+ case 全过（既有 15 改 parametrize + 新增 5 + 1 回归），全量 pytest -q 0 failed"
dependencies:
  - "话题 waker-status-threshold-align（bd057f92）Round 1+2+3 共识收口（已 ready）"
  - "现有 cli/waker_status_view.py:26 LIVE_WINDOW_SECONDS=30 硬编码（root cause）"
  - "现有 server/services/status_service.py:expected_remind_runtime + idle_threshold（T2 busy 拆分 b3ec2e4d 已落地）"
  - "现有 .map/simple-waker-state-{persona}.json 含 pid/cycles_total/reminds_sent_total/skips_unchanged_total/errors_last_n/last_cycle_at/last_poll_at 字段（T5-A 715202a3 I2 实际累加字段，**未序列化** active_interval/idle_interval — 已 reviewer 核证 grep 全 .map/ 无 hit）；active_interval/idle_interval 实际派生源是 cli/simple_waker.py:201 SimpleWakerConfig active_interval 默认 30.0 + :202 idle_interval 默认 300.0（in-memory 启动时）+ :1418 CLI flag --active-interval（运行时 override）"
  - "战役 T5-A（715202a3）闭环后遗留『上线即误报』问题（plan §风险 显式记录 T5-A 同帧一致性测试遗漏）"
  - "T5-A A1 waker 启动 atomic write simple-waker-state-{persona}.json（**不写** active_interval/idle_interval；这两字段从 SimpleWakerConfig in-memory 派生，不在 state.json 持久化路径上）"
  - "验收通过后由监督者重启 server 与 waker 生效（无 docker 镜像 build，仅 daemon restart）"
---

# waker status 阈值口径修正（T6）

## 背景

T5-A（715202a3）落地的 `map waker status` 上线即误报：waker 以 active-interval=30s 正常轮询，poll 间隙常态 26-51s，`map work`（server status_service，T2 口径）三 persona 全 ok，同时刻 `map waker status` 却把 host/reviewer 标 stale。

**根因**：cli/waker_status_view.py:26 `LIVE_WINDOW_SECONDS = 30` 硬编码——waker 轮询间隔本身即 30s，gap > 30s 是必然事件，阈值与轮询周期同量级导致高误报。busy 升级阈值 300s 也远低于 T2 落地的 server 侧 busy 容忍（默认 30min 级）。

**T5-A 闭环遗漏**：plan §验收有"全量测试 1806 passed"但**没强制同帧一致性测试**（`map waker status` 与 `map work` 同时刻对账）；监督者重启 waker 后立即触发常态 stale 误报 → plan §验收缺**实测复核**环节。

## 任务

修复 `map waker status` 阈值口径：派生自 waker 实际轮询配置（active_interval/idle_interval），向 server T2 口径对齐。

## 实施步骤

### I1 抽 `lib/waker_status_config.py` 单模块

- 新建 `lib/waker_status_config.py`（cli + server 都 import 的唯一来源）
- 导出 4 阈值常量函数：
  ```python
  def live_window(active_interval: int) -> int:
      return max(2 * active_interval, 30)        # 2× + floor 30s 防极短
  
  def idle_stale(active_interval: int, idle_interval: int) -> int:
      return max(3 * active_interval, idle_interval)
  
  def dead_window(active_interval: int) -> int:
      return 10 * active_interval                # 远超 1+ idle stale 窗口
  
  def busy_stale(expected_remind_runtime: int, idle_threshold: int) -> int:
      return max(expected_remind_runtime, 2 * idle_threshold)
  ```

### I2 cli/waker_status_view.py 改派生阈值

- 删除硬编码 `LIVE_WINDOW_SECONDS = 30`
- 从 `lib.waker_status_config` 导入
- 状态计算改为调用函数（如 `state = "live" if gap <= live_window(active_interval) else ...`）
- **active_interval 派生源（reviewer 澄清）**：从 `cli/simple_waker.py:201 SimpleWakerConfig.active_interval` 读取（in-memory 启动时配置），不是从 simple-waker-state-*.json 读取（T5-A I2 未序列化此字段）。如需持久化到 state.json，需扩展 simple-waker 写入路径（本实验**不**扩展，保持 I2 实施最简）
- **idle_interval 派生源**：同上 `SimpleWakerConfig.idle_interval`（默认 300.0）
- active_interval 缺失 fallback 默认 30s + `warnings.warn(..., RuntimeWarning)`（case (c) 覆盖）
- 多 waker 配置隔离：每个 waker 独立从 SimpleWakerConfig 派生，**不允许**全局缓存

### I3 server/services/status_service.py 现有常量 refactor

- 现有常量（`expected_remind_runtime` / `idle_threshold`）refactor 到 `lib/waker_status_config.py` 的对应位置（搬位置不改值）
- server 端 import 改为 `from lib.waker_status_config import busy_stale`
- 不动 T2 语义：阈值数值或计算逻辑均保持

### I4 既有 tests/test_waker_status.py 15 case 改 parametrize

- 参数化接收 `active_interval` fixture，按派生公式计算期望
- 断言档位（live/stale/dead/busy_stale）替代绝对值断言
- 避免 active_interval 微调导致测试大规模失败
- 明确豁免"测试要求不变 vs 阈值必须改"死锁

### I5 新增 5 case (a)-(e)

- (a) 派生档位表 case：`gap ∈ {0.5×, 1.5×, 2.5×, 3.5×, 10×} × active_interval` → live/live/live/stale/dead 五档断言
- (b) busy 升级档位 case：`busy_since ∈ {0.8×, 1.5×, 3.0×} × expected_remind_runtime` → live/live/busy_stale
- (c) active_interval 缺失降级 case：mock state.json 无 `active_interval` → cli fallback 30s + WARN
- (d) 多 waker 不同 active_interval case：mock 3 waker active_interval={10, 30, 60} → 各自派生不同 LIVE_WINDOW_SECONDS
- (e) 跨版本兼容 case：T5-A I2 之前 state.json（无 active_interval）→ 同 (c) 降级 + WARN

### I6 回归保护 fixture

- fixture 复现 host 描述"上线即误报"：active_interval=30, gap=51 → 期望 live（不是 stale）
- 确保修复有效

### I7 同帧一致性实测复核（修复 T5-A 闭环遗漏）

- 实测：active-interval=30 的 3 waker 环境下，`map waker status` 与 `map work` 同时刻输出对账
- 期望：均为 live/ok（无 stale 误报）
- 监督者按验收清单实测 X 项并附日志（**plan §验收 硬约束**）

### I8 commit + log + release

- 窄 commit 白名单 `^lib/`、`^cli/`、`^server/services/`、`^tests/`
- ruff check 0 + pytest 全量绿
- complete log → reviewer → done

## 风险与边界

- **不动 waker status 的命令形态/列结构/数据源**（只修阈值口径与测试）
- **不动 server status_service 的 T2 语义**（仅搬常量位置不改值）
- 涉及 cli/ 改动，验收通过后由监督者重启 server 与 waker 生效（无 docker 镜像 build）
- **T5-A 闭环遗漏显式记录**（plan §风险）：
  - 715202a3 漏同帧一致性测试 → 本实验 §验收 A7 硬约束修复
  - 未来 T 类视图实验 plan §验收 必含三项硬约束（写入 SKILL/wake.md）：
    1. **同帧一致性测试**（如适用）
    2. **真实环境实测复核**（监督者实测 + 附日志）
    3. **fail-fast 阈值**（实测误报率 > 5% 不算闭环，必须返工）

## §派生公式设计理由（plan §派生公式 显式记录）

**为什么 `LIVE_WINDOW_SECONDS = max(2 × active_interval, 30)` 用 2× 而不是 1.5× 或 3×**：

- **1.5× 不选**：active_interval=30s 时 1.5×=45s，waker 实际 poll 间隙常态 26-51s（含网络抖动），1.5× 容不下常态抖动，会把"正在跑"误判 stale
- **3× 不选**：active_interval=30s 时 3×=90s，stale 告警窗口 60s 才升级 dead，期间真实 dead 的 waker 不会被及时发现，失去告警意义
- **2× 是折中**：active_interval=30s 时 2×=60s，stale 窗口 30s（30-60s），既抗单次网络抖动又不过于迟钝；60s 仍能及时升级 dead
- **floor 30s 配合**：active_interval 极短场景（5s）下，2×=10s 太短不可用，floor 30s 给突发延迟留余量

## 主题联动：口径三层

T6 闭环后本战役"口径三层"形成完整闭环：

| 层 | 落地 | 职责 |
|----|------|------|
| 状态机口径 | T2 (b3ec2e4d) | server `status_service.py` 定义"什么算活/死/busy 容忍" |
| 视图渲染口径 | T5-A (715202a3) + **T6（本实验）** | cli `map waker status` 复用状态机口径派生阈值（不再硬编码 30s） |
| 视图数据源口径 | T5-A A1 + d0c9dc5f | waker state.json 单源 + drift 自检保鲜 |

**硬约束**（已写入 plan §风险）：
- 视图层**不能**定义自己的阈值常量，必须派生自状态机层
- `lib/waker_status_config.py` 单模块，cli + server 都 import
- 视图类实验 plan §验收 必含"同帧一致性测试"

## Co-author

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
