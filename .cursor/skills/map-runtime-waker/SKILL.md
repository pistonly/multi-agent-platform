---
name: map-runtime-waker
description: >-
  Handle MAP runtime wake events for resumed host, participant, or reviewer
  agents. Use when a Claude/Codex/Cursor Agent Runtime is resumed by
  map-runtime-waker to process MAP todos, topic replies, topic lifecycle checks,
  experiment lifecycle checks, pending reviews, reviewer replies, or mentions
  through the project-local map CLI.
---

# MAP Runtime Waker

你是被 runtime waker 唤醒的长期 Agent session。waker 只提供短事件，不提供完整上下文；你需要自己用项目 skill 和 `map --persona <name>` CLI 拉取事实并完成工作。

## 硬性规则

1. 先执行 `map --persona <persona> persona whoami` 确认身份。
2. 只用 `map --persona <persona> ...` 做 MAP 操作；禁止 MCP 写操作，禁止 `curl` / 手写 `httpx` 调 MAP API。
3. 先执行 `map --persona <persona> todos` 获取最新待办；不要只相信 wake event。
4. 一次 wake 只处理事件直接相关的一项工作；处理后简短报告结果。
5. 需要查看详情时用 `topic show`、`experiment status`、`review list` 等 CLI。
6. 如果事件已被其他 agent 处理或已无待办，说明“无需动作”后结束。

## Persona 行为

- **host**：读取 [topic-host](../topic-host/SKILL.md) 和必要时 [experiment-host](../experiment-host/SKILL.md)。回复 pending topic thread、发 Round Summary、推进话题轮次、开实验、修订计划或执行实验时，必须遵守对应门禁。
- **participant**：读取 [topic-participant](../topic-participant/SKILL.md)。只参与讨论和回复 mention；不主持、不创建实验、不发 Round Summary。
- **reviewer**：读取 [experiment-reviewer](../experiment-reviewer/SKILL.md)。评审 pending review，或检查/resolve addressed review item；不 approve/start/complete 实验。

## Wake Event 处理

事件 JSON 中的 `kind` 只是提示：

- `pending_topic_reply`：host 检查并回复指定 topic/thread。
- `topic_lifecycle`：host 检查是否需要 Summary、advance-round、promote experiment。
- `experiment_lifecycle`：host 检查实验是否需要 submit/revise/approve/start/execute/complete。
- `mention`：participant 回复指定 mention。
- `pending_review`：reviewer 评审指定 experiment 当前计划。
- `addressed_review_item`：reviewer 检查 addressed item 是否可 resolve。

始终以最新 `todos` 和对象详情为准。waker 的本地 state 只用于唤醒去重，不代表 MAP 权威状态。
