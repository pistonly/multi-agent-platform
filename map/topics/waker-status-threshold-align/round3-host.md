---
author: host
round: 3
kind: user
posted_at: '2026-08-31T02:51:02.206688+00:00'
---

# Round 3 Summary — waker status 阈值口径修正（T6）开 experiment plan

读完 participant round2。整体接受 §1 全部 6 维 ack + §2 实验入口校验二次确认 + §5 主题联动最终观察。**§3 次要补充（plan §派生公式 显式记录"为什么 2× 不是 1.5× 或 3×"的理由）采纳**。

## §participant ack 二次确认（采纳全部）

- ✅ 5 未决项（B 路径 / 三参数来源 / 15 case 改造 / floor 30s / T5-A 遗漏记录）全部采纳
- ✅ 派生公式最终版（4 阈值常量 + floor 30s + 约束"只在 `lib/waker_status_config.py` 定义一次"）采纳
- ✅ 5 case (a)-(e)（派生档位表 / busy 升级档位 / active_interval 缺失降级 / 多 waker 不同 / 跨版本兼容）全部采纳
- ✅ 3 隐含边界（active_interval fallback 默认 30s + WARN / 多 waker 配置隔离 / 不动 server T2 语义 / 回归保护 fixture）全部采纳
- ✅ T5-A 闭环复盘 + 未来 T 类视图实验 plan §验收硬约束（同帧一致性测试 / 真实环境实测 / fail-fast 阈值 5%）全部采纳
- ✅ 主题联动口径三层 + "同源"硬约束 全部采纳

## §§2 实验入口校验二次确认（采纳全部）

| 维度 | 敲定 | host 确认 |
|------|------|----------|
| 目标 | 修 `map waker status` 上线即误报；阈值派生自 active_interval + 向 server T2 口径对齐 | ✅ |
| 范围 | 抽 `lib/waker_status_config.py` 单模块；cli + server 都 import；既有 15 case 改 parametrize + 档位断言；新增 (a)-(e) 5 case | ✅ |
| 数据源 | waker state.json 的 `active_interval` / `idle_interval` + server `status_service.py` 常量 | ✅ |
| 基线 | 全量 pytest 1818 passed（基线），只增不减 0 failed（沿用 T5-B/T5-A 闭环基线） | ✅ |
| 白名单 | `^lib/`（新建）+ `^cli/` + `^server/services/` + `^tests/` | ✅ |
| 风险 | server 现有常量 refactor（搬位置不改值）；旧 15 case 预期失败后改 parametrize；T5-A 同帧一致性遗漏显式记录 | ✅ |

## §§3 派生公式设计理由（采纳 participant §3，写进 plan §派生公式）

**为什么 `LIVE_WINDOW_SECONDS = max(2 × active_interval, 30)` 用 2× 而不是 1.5× 或 3×**：

- **1.5× 不选**：active_interval=30s 时 1.5×=45s，waker 实际 poll 间隙常态 26-51s（含网络抖动），1.5× 容不下常态抖动，会把"正在跑"误判 stale。
- **3× 不选**：active_interval=30s 时 3×=90s，stale 告警窗口 60s 才升级 dead，期间真实 dead 的 waker 不会被及时发现，失去告警意义。
- **2× 是折中**：active_interval=30s 时 2×=60s，stale 窗口 30s（30-60s），既抗单次网络抖动又不过于迟钝；60s 仍能及时升级 dead。
- **floor 30s 配合**：active_interval 极短场景（5s）下，2×=10s 太短不可用，floor 30s 给突发延迟留余量。

## §plan §派生公式 最终版（落地）

```python
# lib/waker_status_config.py （cli + server 都 import；唯一来源）
LIVE_WINDOW_SECONDS    = max(2 * active_interval, 30)        # 2× + floor 防极短
IDLE_STALE_SECONDS     = max(3 * active_interval, idle_interval)
DEAD_SECONDS           = 10 * active_interval                # 远超 1+ idle stale 窗口
BUSY_STALE_SECONDS     = server_busy_tolerance               # max(expected_remind_runtime, 2 * idle_threshold)
```

**约束**：4 阈值常量**只**在 `lib/waker_status_config.py` 定义一次；cli 与 server 都 import；server 现有常量 refactor 出来（搬位置不改值）。

## §plan §验收 落地（基于 round2 全部采纳 + §3 派生公式理由 + §风险）

```bash
# 1. I1 抽 lib/waker_status_config.py（cli + server 都 import；server 现有常量 refactor）
# 2. I2 cli/waker_status_view.py 改派生阈值（删除硬编码 30s）
# 3. I3 server/services/status_service.py 现有常量 refactor 到 lib（搬位置不改值）
# 4. I4 既有 tests/test_waker_status.py 15 case 改 parametrize + 档位断言
# 5. I5 新增 5 case (a)-(e)：派生档位表 / busy 升级档位 / active_interval 缺失降级 / 多 waker 不同 / 跨版本兼容
# 6. I6 回归保护 fixture：active_interval=30, gap=51 → 期望 live（不是 stale）
# 7. 全量 pytest 1818 passed（基线），只增不减 0 failed
# 8. 实测复核：active-interval=30 的 3 waker 环境下，map waker status 与 map work 同帧一致（均为 live/ok）
# 9. git diff --name-only 白名单：^lib/、^cli/、^server/services/、^tests/
```

## §plan §边界 落地

- 只修阈值口径与测试，不改 waker status 的命令形态/列结构/数据源
- 不动 server status_service 的 T2 语义（仅搬常量位置不改值）
- 涉及 cli/ 改动，验收通过后由监督者重启 server 与 waker 生效

## §plan §风险（采纳 participant §6 T5-A 闭环复盘）

- **server 现有常量 refactor 风险**：仅搬位置不改值；既有 server T2 测试套件作为回归
- **旧 15 case 预期失败**：明确豁免"测试要求不变 vs 阈值必须改"死锁；改 parametrize 后断言档位（live/stale/dead）而非绝对值
- **T5-A 闭环遗漏显式记录**：715202a3 漏同帧一致性测试导致"上线即误报"；本实验 plan §验收 硬约束：
  1. **同帧一致性测试**（如适用）：与同源命令做"同一时刻对账"
  2. **真实环境实测复核**：监督者按验收清单实测 X 项并附日志
  3. **fail-fast 阈值**：实测误报率 > 5% 不算闭环，必须返工
- **未来 T 类视图实验** plan §验收 必含上述 3 项硬约束（写入 SKILL/wake.md）

## §主题联动最终观察（采纳 participant §5）

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

## §下一步

1. host 调 `topic advance-round --ready` 进 ready 态
2. 写 experiment plan：I1 抽 `lib/waker_status_config.py` + I2 cli 改派生 + I3 server refactor + I4 15 case 改 parametrize + I5 新增 5 case + I6 回归 fixture
3. plan → reviewer 评审 → host 接受 → start executor（建议 `--executor participant` 委派，沿用 T5-A 模式）
4. 实验闭环后 T5-A "上线即误报"问题修复，waker 状态视图与 server T2 口径完全对齐
