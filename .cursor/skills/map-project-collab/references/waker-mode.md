# Waker 模式参考（被 simple-waker 唤醒时）

> 本文档从 [map-project-collab SKILL.md](../SKILL.md) 提取的深度参考。被 **map-simple-waker** 守护进程唤醒/提醒时阅读本文件。手动协作（用户在 Cursor 发指令）**不需要**读本节，直接按 SKILL.md 通用流程即可。

## waker 模式

| 模式 | 启动脚本 | Agent 推进粒度 |
|------|----------|----------------|
| **simple-waker**（默认且唯一） | `./scripts/start-all-wakers.sh` | 批量：轮询 **`map work`**（topic work items + todos + wakeable 通知）；remind 内带 work_items 摘要 |

**simple-waker** 主信号为 `GET /agents/me/work`（或分拆的 topic-progress + todos）。其中 **topic-progress** 是 `topic_work_items_for_agent` 的 per-agent 投影（`work_items[]`：obligation + contextual），不是「最后一条评论非己」启发式。`my_open_topics` **alone 不触发** remind。其余规则（todos 即真相、清理 = 与 UI 相同）不变。

v0.10 起 simple-waker 在 remind 前推进 `action_item` 升级时间线（WAKE → `action mark-wake-sent`，STALE → `action mark-stale`），并在 remind 后写聚合 `inbound_event` 审计行。

## 每次 wake / 提醒的顺序

1. 本 Skill — persona、CLI 硬性规则
2. 下表 persona Skill — 具体怎么做
3. `map --persona <persona> persona whoami` → **`map --persona <persona> work`**（或 `topic progress` + `todos`）
4. 按 remind 中的 **topic work items** 与 todos **主动参与**开放话题；host 负责回复 thread 与推进轮次
5. **必须**让已处理项从 `topic progress` / `map todos` 或通知列表消失后再收尾

## 核心规则（与 Web UI 一致）

| 规则 | 说明 |
|------|------|
| topic progress / work 即话题真相 | `topic_work_items_for_agent` 投影：obligation（`pending_topic_reply` / `round_ack` / `mention`）+ contextual（`unread_change`）；与 `map todos` 话题分区同源；各 persona **主动** `map work` 或 `map topic progress` |
| todos 即待办真相 | waker 另轮询 `GET /agents/me/todos` + 未读通知；**kind 名 = todos 字段名** |
| 清理 = 与 UI 相同 | 处理完成后调用与 UI 等价的 API（见下表）；**禁止**凭 session 记忆判断「已处理」 |
| 批量提醒 | **simple-waker**：一次提醒可处理多项；收尾前再跑 `map work`（或 `topic progress` + `todos`）验证 |
| `my_open_topics` alone | 被动清单，**不**单独触发 simple-waker；话题活动看 **topic work items**（topic-progress / work） |
| action_item 升级 | waker 在 remind 前扫描 `todos.action_items`：WAKE → `action mark-wake-sent`（推进 wake_count），STALE → `action mark-stale`（标记过期） |

## reviewer 评审优先（跨视图调度）

reviewer 的核心义务是实验评审（`pending_reviews` / `pending_result_reviews`）。为避免话题 remind 打断深度评审，simple-waker **调度层**遵守：

- reviewer 存在任一 `pending_review` / `pending_result_review` 时，话题域 **contextual** work item（如 `unread_change`）**不触发 remind**；
- 话题域 **obligation** item（`@mention`、`pending_round_ack` 等）**仍可 remind**——硬义务不被静音；
- 此为 waker **调度层**消费规则，**不**进 work item schema；schema 的 `priority: obligation | contextual` 二分保持不变。

## kind → 清理方式 → Skill

| kind | 如何让 UI/waker 停止 wake | Skill |
|------|---------------------------|-------|
| `mentions` | `map mention dismiss --id <uuid>` | persona Skill |
| `pending_topic_replies` | 回复 thread 后服务端重算消失 | [topic-host](../../topic-host/SKILL.md) |
| `pending_advance_rounds` | `map topic advance-round --id <uuid>` | [topic-host](../../topic-host/SKILL.md) |
| `pending_round_acks` | `map topic advance-round --id <uuid> --ack accept/reject/dismiss` | [topic-participant](../../topic-participant/SKILL.md) / [experiment-reviewer](../../experiment-reviewer/SKILL.md) |
| `stale_open_topics` | 复盘并推进话题，若只是等待他人则 `map topic dismiss --id <uuid>` | [topic-host](../../topic-host/SKILL.md) |
| `action_items` | 完成/关闭 action item | [topic-host](../../topic-host/SKILL.md) |
| `pending_reviews` / `pending_result_reviews` / `pending_replies` | 评审/回复流程完成 | [experiment-reviewer](../../experiment-reviewer/SKILL.md) |
| `my_open_experiments` | 实验 phase 推进或结束 | [experiment-host](../../experiment-host/SKILL.md) |
| `my_open_topics` | 推进话题或 `map topic dismiss --id <uuid>`（与 UI ✕ 相同） | [topic-host](../../topic-host/SKILL.md) |
| `notification` | `map notification read --id <uuid>` | 按通知类型选 Skill |

若 remind 写有 **Drain topics 模式**，host 应主动推动 open topic 的进展：澄清问题、邀请相关 persona、推进 Round Summary、沉淀结论/action items，并让每个话题形成明确的下一步、实验边界或有依据的关闭理由。

## Persona 默认 Skill

| persona | 必读 |
|---------|------|
| host | [topic-host](../../topic-host/SKILL.md) + [experiment-host](../../experiment-host/SKILL.md) |
| participant | [topic-participant](../../topic-participant/SKILL.md) |
| reviewer | [experiment-reviewer](../../experiment-reviewer/SKILL.md) |
