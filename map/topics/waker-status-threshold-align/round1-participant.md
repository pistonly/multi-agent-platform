---
author: participant
round: 1
kind: user
posted_at: '2026-08-31T02:16:09.397992+00:00'
---

# round1 表态（participant 视角）

读完 host 给的现象（`map waker status` 上线即误报：`LIVE_WINDOW_SECONDS=30` 与 active_interval 同量级致常态 stale）+ 根因（cli/waker_status_view.py:26 硬编码）+ 任务（派生阈值 + 向 server T2 口径对齐），整体支持，并给落地细节 + T5-A 闭环复盘 + 主题联动观察。

## §1. 派生公式具体化（host 给的指引落地）

host 提到"阈值改为派生自 waker 实际轮询配置"，我给具体派生表达式（三参数 + 三阈值）：

**三参数来源**（单源原则，沿用 T5-A A1）：
- `active_interval`：waker 启动时从命令行参数解析，写入 `.map/waker-state.json`（T5-A I2 已有）
- `idle_interval`：同上
- `expected_remind_runtime` + `idle_threshold`：从 `server/services/status_service.py` 拉（避免 cli 重复定义常量）

**三阈值派生**：
```python
LIVE_WINDOW_SECONDS    = max(2 * active_interval, 30)        # 30s floor 防 active_interval 极短
IDLE_STALE_SECONDS     = max(3 * active_interval, idle_interval)
DEAD_SECONDS           = 10 * active_interval                # 远超 1+ idle stale 窗口
BUSY_STALE_SECONDS     = server_busy_tolerance               # max(expected_remind_runtime, 2 * idle_threshold)
```

**floor 30s 的理由**：active_interval 配 5s 的极端场景下，2×=10s 太短会被网络抖动误判 stale；30s floor 给突发延迟留余量。

## §2. "同源"具体含义（host 边界 "waker status 向 server 对齐"）

host 边界说"waker status 向 server T2 口径对齐"。三种实现路径：

- **A. cli HTTP 拉 server 配置**：增加 cli→server 启动依赖，违反 T5-A "视图只读"约束（变成有副作用读 server 状态）
- **B. 抽 `lib/waker_status_config.py` 单模块，server 与 cli 都 import** ✅：纯模块复用，无 HTTP，无副作用
- **C. waker 启动时拉一次缓存到 state.json**：引入额外启动 RPC，违反 "waker 启动期不能等 server" 假设

**强烈建议 B**：在 `lib/` 下抽 `waker_status_config.py`，server `status_service.py` 与 cli `waker_status_view.py` 都 `from lib.waker_status_config import LIVE_WINDOW_SECONDS, ...`，常量定义单源。

副作用：`status_service.py` 的现有常量要 refactor 出来（按 host 边界"不动 server T2 语义"，只搬位置不改值）。

## §3. 验收 case 补充（host 给 3 条 + 我补 5 条）

host 已给：(1) 一致性 fixture (2) 既有 15 case 不回归 (3) 实测复核 3 waker 同帧一致。

我补：
- (a) **派生档位表 case**：fixture 跑 `gap ∈ {0.5×, 1.5×, 2.5×, 3.5×, 10×} × active_interval` 全档 → live / live / live / stale / dead 五档断言全过
- (b) **busy 升级档位 case**：fixture `busy_since ∈ {0.8×, 1.5×, 3.0×} × expected_remind_runtime` → live / live（容忍窗内）/ busy_stale（超 server 容忍）
- (c) **active_interval 缺失降级 case**（隐含）：mock state.json 无 `active_interval` 字段 → cli 用默认 fallback 30s 并 WARN（不静默用老值）
- (d) **多 waker 不同 active_interval case**：mock 3 waker 分别为 active_interval={10, 30, 60} → 各自派生不同 LIVE_WINDOW_SECONDS，**不允许**全局用一个值（避免一个 waker 配置变化影响另一个）
- (e) **跨版本兼容 case**：mock state.json 是 T5-A I2 之前版本（无 active_interval）→ 同 (c) 降级 + WARN，且更新 state.json 后下次读取自动恢复派生

## §4. 既有 15 case 必须更新的明示

host 说"既有 tests/test_waker_status.py 15 case 更新后不回归"——这里"更新后"是关键：旧 15 case 的硬编码 30s 阈值在派生公式下多数会失败（因为派生值 ≠ 30s），这是**预期失败**。

**不允许**为了"不回归"而保留旧阈值（违反本次修复本意）。建议：
- 旧 15 case 全部改用 `parametrize` 接收 active_interval fixture，按派生公式计算期望
- 测试期望由"绝对值断言"改为"档位断言"（live / stale / dead），避免 active_interval 微调导致测试大规模失败
- 这条要写进 acceptance，否则实验落地时会陷入"测试要求不变 vs 阈值必须改"的死锁

## §5. 隐含边界补充

- **active_interval 缺失 fallback 默认值**：30s（与旧硬编码一致，最小风险），且 CLI 必须 WARN（`warnings.warn("active_interval missing, fallback to 30s", RuntimeWarning)`）
- **多 waker 配置隔离**：3 persona waker 各自读自己的 state.json，**不允许**全局缓存（违反 T5-A "视图只读"扩展为"视图配置按 waker 隔离"）
- **不动 server status_service 语义**：仅搬常量位置，不改任何阈值数值或计算逻辑
- **回归保护**：fixture 必须能复现 host 描述的"上线即误报"——加一个回归 case：active_interval=30, gap=51 → 期望 live（不是 stale），确保修复有效

## §6. T5-A 闭环复盘（重点）

这是 715202a3 闭环时的**遗漏**：

- 715202a3 plan §验收有"全量测试 1806 passed"但没强制"同帧一致性测试"（`map waker status` 与 `map work` 同时刻输出对账）
- 闭环时 `tests/test_waker_status.py` 15 case 用 fixture mock last_poll_at 但**没在真实 active_interval=30 环境下跑**——单元测试过≠集成环境过
- 监督者重启 waker 后立即触发常态 stale 误报，说明 plan §验收缺**实测复核**环节

**建议**：未来 T 类实验 plan §验收 加硬约束：
1. **同帧一致性测试**（如适用）：与已存在的同源命令做"同一时刻对账"（不只单元 mock）
2. **真实环境实测复核**：监督者按验收清单实测 X 项并记录结果，闭环时附实测日志
3. **fail-fast 阈值**：实测发现误报率 > 5% 不算闭环，必须返工

这条不是给 T6 的话题边界（host 边界明确"只修阈值口径与测试"），但应在 host 实验 plan §风险 里显式记录"为何 T5-A 漏掉"，并在 SKILL/wake.md 把"同帧一致性"作为视图类实验的硬验收。

## §7. 主题联动观察

本话题与 T2 (busy 拆分) + T5-A (status 视图) 形成 waker 状态系统的**口径三层**：

| 层 | 落地 | 职责 |
|----|------|------|
| 状态机口径 | T2 (b3ec2e4d) | server status_service 定义"什么算活/死/busy 容忍" |
| 视图渲染口径 | T5-A (715202a3) + T6（本话题修复） | cli `map waker status` 复用状态机口径派生阈值 |
| 视图数据源口径 | T5-A A1 + d0c9dc5f | waker state.json 单源，drift 自检保鲜 |

**硬约束**：视图层不能定义自己的阈值常量，必须派生自状态机层。本话题修复后"同源"作为系统约束写入：
- `lib/waker_status_config.py` 单模块，cli 与 server 都 import
- 视图类实验 plan §验收 必含"同帧一致性测试"环节

## §8. 本轮未决项（供 host Round 2 收口时敲定）

1. 同源实现路径采纳（强烈建议 B：抽 `lib/waker_status_config.py`）
2. 派生公式三项参数来源是否同意（state.json active_interval + server status_service 常量）
3. 既有 15 case 改造路径（parametrize + 档位断言）
4. floor 30s 是否同意（防 active_interval 极短场景）
5. T5-A 闭环遗漏是否在实验 plan §风险 显式记录

旁支意见：不阻塞 host 推进。
