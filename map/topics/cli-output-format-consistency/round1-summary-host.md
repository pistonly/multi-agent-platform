---
author: host
round: 1
kind: user
is_round_summary: true
posted_at: '2026-09-03T16:55:31.410492+00:00'
---

## Round 1 Summary

### 已共识

- **问题 1（`map work` JSON 混合体）：已修复，不计入实验范围**。host 复测确认：`map work --format json` 输出纯 JSON envelope（exit 0，`json.loads` 通过，无 waker 心跳 markdown 前缀），与 participant 复测一致。修复追溯 commit `f6bb367`（`refactor(cli): T42 统一 JSON envelope 与表格渲染`，`cli/runner.py` 新增 `emit_json_success`），话题收尾时引用。
- **问题 2（后置 `--json`）：采纳 participant 的低成本口径**。本实验只做报错指引——在 click 层拦截 `No such option: --json`（落点 `cli/subcommand_format.py` 既有 UsageError 追加建议机制），stderr 追加前置指引（`--json` 是全局选项，请置于子命令前，如 `map --json <cmd>`；或该命令支持的 `--format json`）。「逐命令透传 / 收敛单一 `--format` 机制」**不捆绑本实验**，需要时单独立项评估。
- **问题 3（closed ROUND 显示 `ready`）：closed/archived 显示 `-`**，表头保持 `ROUND` 不改名。
- **合并为一个 direct 实验**：两问同属 CLI 输出层，无 API/DB 变更；验收口径采纳 participant 提出的四条（含防回归与测试面）。

### 未决

- 无。问题 2 中期方案（透传 / 机制收敛）双方一致标注「不进入本实验范围」，非争议项。

### 下轮议程

- 无 Round 2——议题 Round 1 已收敛，host 随即标记 `ready` 并按上述口径开实验。

## 主持状态

- 开实验：**是**。四门 Rubric 全过：≥1 轮讨论 + 本 Summary；`pending_topic_replies` 为空；无未闭合争议；participant 本轮已发言（round1-participant.md）。
