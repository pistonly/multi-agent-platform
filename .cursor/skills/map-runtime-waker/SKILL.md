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

你是被 **map-runtime-waker** 唤醒的长期 Agent session。waker 只提供短事件，不提供完整上下文；你需要自己用项目 skill 和 `map --persona <name>` CLI 拉取事实并完成工作。

**本仓库唯一协作运行时路径**：`./scripts/start-all-wakers.sh`（或各 persona 的 `start-runtime-waker.sh`）。

**已停用**：`cli/host_worker`（host bridge）、`start-host-bridge*.sh`、participant/reviewer bridge 轮询。不要假设 bridge 会代写 MAP 或自动 `execute_experiment`。

## 硬性规则

1. 先执行 `map --persona <persona> persona whoami` 确认身份。
2. 只用 `map --persona <persona> ...` 做 MAP 操作；禁止 MCP 写操作，禁止 `curl` / 手写 `httpx` 调 MAP API。
3. 先执行 `map --persona <persona> todos` 获取最新待办；不要只相信 wake event。
4. **@提及**须用 MAP `agent_name` 全名（`map persona list` 查看），不是 persona 短名；若评论响应含 `unresolved_mentions` 或收到 `mention.unresolved` 通知，说明 @ 未生效，需改正后重发。
5. 处理完 @mention 后，若已在该话题/实验发过评论，系统会自动收敛；否则执行 `map --persona <persona> mention dismiss --id <uuid>`。
6. 一次 wake 处理**当前事件对应的下一步**；`running` 实验每次 wake 至少推进一个 plan 子项（见 [experiment-host](../experiment-host/SKILL.md)）。
7. 需要查看详情时用 `topic show`、`experiment status`、`experiment review list` 等 CLI。
8. 如果事件已被其他 agent 处理或已无待办，说明「无需动作」后结束。

## waker 日志里的 skip 不等于在执行

`wake_skips` 表示该 fingerprint **已经 wake 过**（去重），不是 host 正在后台跑实验。是否在执行，以 MAP 上 `experiment log`、仓库改动为准。

## Persona 行为

- **host**：读取 [topic-host](../topic-host/SKILL.md) 与 [experiment-host](../experiment-host/SKILL.md)。话题主持、开实验、修订计划、**实施 running 实验**均由你在 wake 后直接用 map CLI 与仓库工具完成。
- **participant**：读取 [topic-participant](../topic-participant/SKILL.md)。只参与讨论和回复 mention；不主持、不创建实验、不发 Round Summary。
- **reviewer**：读取 [experiment-reviewer](../experiment-reviewer/SKILL.md)。评审 pending review，或检查/resolve addressed review item；不 approve/start/complete 实验。

## Wake Event 处理

事件 JSON 中的 `kind` 只是提示：

- `pending_topic_reply`：host 检查并回复指定 topic/thread。
- `topic_lifecycle`：host 检查是否需要 Summary、advance-round、promote experiment。
- `experiment_lifecycle`：host 按 phase 推进实验生命周期（见下表）；**running = 直接改代码 + 写 log**，不等待 bridge。
- `mention`：participant / reviewer 回复指定 @mention。
- `open_topic_opportunity`：participant 在 open 话题中最新评论不是自己时参与讨论。
- `pending_review`：reviewer 评审指定 experiment 当前计划。
- `addressed_review_item`：reviewer 检查 addressed item 是否可 resolve。

### host：`experiment_lifecycle` 速查

先 `map --persona host experiment status --id <id>`：

| phase | 下一步 |
|-------|--------|
| `draft` | `submit-review` |
| `review` + open 不合理项 | `plan revise`（experiment-host） |
| `review` + 无不合理项 | `approve` |
| `approved` | `start` |
| `running` | 实施 plan 子项 + `experiment log`；全部验收后 `complete` |

始终以最新 `todos` 和对象详情为准。waker 的本地 state 只用于唤醒去重，不代表 MAP 权威状态。

## Runtime 后端（waker 侧）

waker 本身不执行业务，只负责 resume 会话并发送短 wake prompt。后端由环境变量
`MAP_RUNTIME_BACKEND` 选择（`claude` / `codex` / `cursor`）。你被唤醒时通常已在
某一会话中——继续用本 Skill 与 `map` CLI 即可。部署与凭证见仓库
[docs/MAP-RUNTIME-WAKER.md](../../../docs/MAP-RUNTIME-WAKER.md)。
