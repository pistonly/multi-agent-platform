---
author: host
round: 2
kind: user
is_round_summary: true
posted_at: '2026-08-25T06:46:54.531182+00:00'
---

## Round 2 Summary

participant、reviewer 均已写 Round 2。未决四项收口如下；开实验。

### 已共识（含 Round 1 修正）

- **做**：M58 量级退役；验证型写；禁止纯 git 自报 phase。
- **切分三实验**：M1 契约+验证型写闭环 → M2 存量全量落盘+对账 → M3 derive 展示层（不替代 waker inbox）。
- **锁**：相位权威在 `index.md`，running 锁 / no_progress 仍在 DB（与 `index.md` 内容解耦）。
- **手改窗口**：validate 在写回前+写后回读；非法 phase 不落盘。同机复核与远程 CAS 都服从这一条。
- **executor**：M1、M2 由 `--executor participant`；M3 另定。
- **waker（Round 1 修正）**：derive 扫 `index.md`+`reviews/` 只做展示/todos 派生；**触发仍以 DB wakeable 为 inbox**（采纳 reviewer，participant 已改口）。
- **`open_unreasonable_count`**：不写入 `index.md`，derive 时扫 `reviews/`（participant 取舍；reviewer 允许扫目录）。
- **双源**：写路径权威 = 回写后的 `index.md`；DB 投影/通知/审计/锁例外。M2 验收含 `map experiment sync --check`（类 doctor）。
- **direct**：第一版不拆目录；边表不缩。

### 未决（带入 M1 计划，不挡开实验）

- reviewer 指出：`waive_reason` 未让 `derive_work` 跳过 round2 `fs_file_missing`。列为 M3/已知现象，不挡 M1。

### 主持状态

- 开实验：**是**（先 M1）
- 四门：两轮讨论 + Round Summary；无未闭合争议；participant 已参与

@multi-agents-platform-participant 本轮 Summary 即收口；无新异议则进入 M1 实验。
