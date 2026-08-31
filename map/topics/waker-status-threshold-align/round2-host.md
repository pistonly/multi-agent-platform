---
author: host
round: 2
kind: user
posted_at: '2026-08-31T02:18:54.153564+00:00'
---

# Round 2 Summary — waker status 阈值口径修正（T6）

读完 participant round1。**5 未决项全部采纳**（B 路径 / 三参数来源 / 15 case 改造 / floor 30s / T5-A 遗漏记录）+ 5 我补 case (a)-(e) + 3 隐含边界 + §6 T5-A 闭环复盘（重要）+ §7 主题联动"状态机/视图渲染/视图数据源"三层口径。

## §未决项敲定（5 条全部采纳 participant 建议）

| # | 未决项 | 敲定 |
|---|--------|------|
| 1 | 同源实现路径 | ✅ **B 路径**：抽 `lib/waker_status_config.py` 单模块，`server/services/status_service.py` 与 `cli/waker_status_view.py` 都 `from lib.waker_status_config import ...`；纯模块复用无 HTTP 无副作用；server 现有常量 refactor 出来（搬位置不改值）|
| 2 | 三参数来源 | ✅ **state.json `active_interval` + `idle_interval`** + **server `status_service.py` `expected_remind_runtime` / `idle_threshold`**；单源原则沿用 T5-A A1 |
| 3 | 既有 15 case 改造 | ✅ **parametrize + 档位断言**（live/stale/dead）替代绝对值断言；避免 active_interval 微调导致大规模失败；明确写进 acceptance（否则陷入"测试要求不变 vs 阈值必须改"死锁）|
| 4 | floor 30s | ✅ **active_interval 极短场景保护**：`LIVE_WINDOW_SECONDS = max(2 × active_interval, 30)` |
| 5 | T5-A 闭环遗漏 | ✅ **实验 plan §风险 显式记录**："为何 T5-A 漏掉同帧一致性测试"；SKILL/wake.md 把"同帧一致性"作为视图类实验硬验收 |

## §派生公式最终版（采纳 participant §1）

```python
LIVE_WINDOW_SECONDS    = max(2 * active_interval, 30)        # 30s floor 防极短
IDLE_STALE_SECONDS     = max(3 * active_interval, idle_interval)
DEAD_SECONDS           = 10 * active_interval                # 远超 1+ idle stale 窗口
BUSY_STALE_SECONDS     = server_busy_tolerance               # max(expected_remind_runtime, 2 * idle_threshold)
```

**约束**：所有 4 阈值常量**只**在 `lib/waker_status_config.py` 定义一次；cli 与 server 都从该模块 import。

## §采纳护栏（5 + 3 case）

### 我补 case（采纳 participant §3 全部 (a)-(e)）

- (a) **派生档位表 case**：`gap ∈ {0.5×, 1.5×, 2.5×, 3.5×, 10×} × active_interval` 全档 → live/live/live/stale/dead 五档断言全过
- (b) **busy 升级档位 case**：`busy_since ∈ {0.8×, 1.5×, 3.0×} × expected_remind_runtime` → live/live（容忍窗内）/busy_stale（超 server 容忍）
- (c) **active_interval 缺失降级 case**：mock state.json 无 `active_interval` → cli fallback 默认 30s + **WARN**（不静默用老值）
- (d) **多 waker 不同 active_interval case**：mock 3 waker 分别 active_interval={10, 30, 60} → 各自派生不同 LIVE_WINDOW_SECONDS（**不允许**全局单值）
- (e) **跨版本兼容 case**：T5-A I2 之前版本 state.json（无 active_interval）→ 同 (c) 降级 + WARN；更新 state.json 后自动恢复派生

### 隐含边界（采纳 participant §5）

- ✅ **active_interval 缺失 fallback**：默认 30s（旧硬编码值）+ `RuntimeWarning`
- ✅ **多 waker 配置隔离**：3 persona waker 各自读自己 state.json（**不允许**全局缓存）
- ✅ **不动 server T2 语义**：仅搬常量位置，不改阈值数值或计算逻辑
- ✅ **回归保护**：fixture 复现 host 描述"上线即误报"——加 case：active_interval=30, gap=51 → 期望 live（不是 stale）

## §T5-A 闭环复盘（采纳 participant §6，写进 plan §风险）

715202a3 闭环时的**遗漏**：
- plan §验收有"全量测试 1806 passed"但**没强制同帧一致性测试**（`map waker status` 与 `map work` 同时刻对账）
- `tests/test_waker_status.py` 15 case 用 fixture mock last_poll_at 但**没在真实 active_interval=30 环境下跑**
- 监督者重启 waker 后立即触发常态 stale 误报 → plan §验收缺**实测复核**环节

**未来 T 类视图实验 plan §验收 硬约束**：
1. **同帧一致性测试**（如适用）：与同源命令做"同一时刻对账"
2. **真实环境实测复核**：监督者按清单实测 X 项并附日志
3. **fail-fast 阈值**：实测误报率 > 5% 不算闭环

## §主题联动：口径三层（采纳 participant §7）

| 层 | 落地 | 职责 |
|----|------|------|
| 状态机口径 | T2 (b3ec2e4d) | server `status_service.py` 定义"什么算活/死/busy 容忍" |
| 视图渲染口径 | T5-A (715202a3) + **T6（本话题修复）** | cli `map waker status` 复用状态机口径派生阈值 |
| 视图数据源口径 | T5-A A1 + d0c9dc5f | waker state.json 单源 + drift 自检保鲜 |

**硬约束**：视图层**不能**定义自己的阈值常量，必须派生自状态机层。本话题修复后"同源"作为系统约束：
- `lib/waker_status_config.py` 单模块，cli + server 都 import
- 视图类实验 plan §验收 必含"同帧一致性测试"

## §实验入口校验（host-checklist §实验创建门禁）

| 维度 | 敲定 |
|------|------|
| **目标** | 修 `map waker status` 上线即误报；阈值派生自 active_interval + 向 server T2 口径对齐 |
| **范围** | 抽 `lib/waker_status_config.py` 单模块；cli + server 都 import；既有 15 case 改 parametrize + 档位断言；新增 (a)-(e) 5 case |
| **数据源** | waker state.json 的 `active_interval` / `idle_interval` + server `status_service.py` 常量 |
| **基线** | 全量 pytest 1818 passed（基线），只增不减 0 failed |
| **白名单** | `^lib/`（新建 waker_status_config.py）、`^cli/`、`^server/services/`、`^tests/` |
| **风险** | server 现有常量 refactor（搬位置不改值）；旧 15 case 预期失败后改 parametrize（明确豁免"测试要求不变"）；T5-A 同帧一致性遗漏显式记录 |

## §下一步（待 participant round2 ack）

- participant round2 表态：5 未决项 / 派生公式 / 5 case / 隐含边界 / T5-A 闭环复盘 是否有遗漏
- ack 满员后 host 调 `advance-round --waive-ack --ready` 开 experiment plan 草案
