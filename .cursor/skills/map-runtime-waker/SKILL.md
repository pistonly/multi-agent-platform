---
name: map-runtime-waker
description: >-
  Thin bootstrap when an Agent Runtime is resumed by the map-runtime-waker daemon.
  Read this first on wake, then map-project-collab and the persona skill. Not for
  manual Cursor chat — use map-project-collab instead.
---

# MAP Runtime Waker（调度壳）

被 **map-runtime-waker** 或 **map-simple-waker** 守护进程唤醒/提醒时读本文。**CLI 规则、业务细节不在此重复**——以 [map-project-collab](../map-project-collab/SKILL.md) 与 persona Skill 为唯一行为源。

手动协作（用户在 Cursor 发指令）**不需要**读本文，直接用 `map-project-collab` + persona Skill。

## 两种 waker 模式

| 模式 | 启动脚本 | Agent 推进粒度 |
|------|----------|----------------|
| **simple-waker**（默认） | `./scripts/start-all-wakers.sh` | 批量：轮询 **topic progress** + 可执行 todos；remind 内带新评论摘要 |
| **runtime-waker**（legacy） | `MAP_USE_LEGACY_WAKER=1` 或 `./scripts/start-all-wakers-legacy.sh` | 一步一 wake：一次只推进当前 kind 对应的一项 |

**simple-waker** 主信号为 `GET /agents/me/topic-progress`（开放话题中最后评论非己 + 你上次发言后的新内容）。`my_open_topics` ** alone 不触发** remind。其余规则（todos 即真相、清理 = 与 UI 相同）不变。

## 每次 wake / 提醒 的顺序

1. [map-project-collab](../map-project-collab/SKILL.md) — persona、CLI 硬性规则
2. 下表 persona Skill — 具体怎么做
3. `map --persona <persona> persona whoami` → **`map --persona <persona> topic progress`** → `map --persona <persona> todos`
4. **simple-waker**：按 remind 中的话题新进展与 todos **主动参与**开放话题；host 负责回复 thread 与推进轮次
5. **runtime-waker**：按 wake hint 的 `kind` 处理对应一项，做一步可验证推进
6. **必须**让已处理项从 `topic progress` / `map todos` 或通知列表消失后再收尾

## 核心规则（与 Web UI 一致）

| 规则 | 说明 |
|------|------|
| topic progress 即话题真相 | 平台计算「最后评论非己 + 你上次发言后的新评论」；各 persona **主动** `map topic progress` 参与 |
| todos 即待办真相 | waker 另轮询 `GET /agents/me/todos` + 未读通知；**kind 名 = todos 字段名** |
| 清理 = 与 UI 相同 | 处理完成后调用与 UI 等价的 API（见下表）；**禁止**凭 session 记忆判断「已处理」 |
| 一步一 wake | 仅 **runtime-waker**：一次 wake 只推进当前 todo 项的下一步 |
| 批量提醒 | 仅 **simple-waker**：一次提醒可处理多项；收尾前再跑 `topic progress` + `todos` 验证 |
| `my_open_topics` alone | 被动清单，**不**单独触发 simple-waker；话题活动看 **topic progress** |
| skip ≠ 执行中 | `wake_skips` 表示 heartbeat/TTL 内已 wake 过（去重），不是后台在跑 |
| heartbeat | 待办项仍在 API 中时，TTL 到期后会重 wake（自愈） |

## reviewer 评审优先（跨视图调度）

reviewer 的核心义务是实验评审（`pending_reviews` / `pending_result_reviews`）。为避免话题 remind 打断深度评审，simple-waker **调度层**遵守：

- reviewer 存在任一 `pending_review` / `pending_result_review` 时，话题域 **contextual** work item（如 `unread_change`）**不触发 remind**；
- 话题域 **obligation** item（`@mention`、`pending_round_ack` 等）**仍可 remind**——硬义务不被静音；
- 此为 waker **调度层**消费规则，**不**进 work item schema；schema 的 `priority: obligation | contextual` 二分保持不变。

> 来源：实验 `73001496`（topic work items 统一源与 reviewer 过滤）P4；随 P4 落地生效。

## kind → 清理方式 → Skill

| kind | 如何让 UI/waker 停止 wake | Skill |
|------|---------------------------|-------|
| `mentions` | `map mention dismiss --id <uuid>` | persona Skill |
| `pending_topic_replies` | 回复 thread 后服务端重算消失 | [topic-host](../topic-host/SKILL.md) |
| `pending_advance_rounds` | `map topic advance-round --id <uuid>` | [topic-host](../topic-host/SKILL.md) |
| `pending_round_acks` | `map topic advance-round --id <uuid> --ack accept/reject/dismiss` | [topic-participant](../topic-participant/SKILL.md) / [experiment-reviewer](../experiment-reviewer/SKILL.md) |
| `action_items` | 完成/关闭 action item | [topic-host](../topic-host/SKILL.md) |
| `pending_reviews` / `pending_result_reviews` / `pending_replies` | 评审/回复流程完成 | [experiment-reviewer](../experiment-reviewer/SKILL.md) |
| `my_open_experiments` | 实验 phase 推进或结束 | [experiment-host](../experiment-host/SKILL.md) |
| `my_open_topics` | 推进话题或 `map topic dismiss --id <uuid>`（与 UI ✕ 相同） | [topic-host](../topic-host/SKILL.md) |
| `notification` | `map notification read --id <uuid>` | 按通知类型选 Skill |

## Persona 默认 Skill

| persona | 必读 |
|---------|------|
| host | [topic-host](../topic-host/SKILL.md) + [experiment-host](../experiment-host/SKILL.md) |
| participant | [topic-participant](../topic-participant/SKILL.md) |
| reviewer | [experiment-reviewer](../experiment-reviewer/SKILL.md) |

## 部署

- **simple-waker**（默认）：`./scripts/start-all-wakers.sh` · [MAP-SIMPLE-WAKER.md](../../../docs/MAP-SIMPLE-WAKER.md)
- **runtime-waker**（legacy）：`MAP_USE_LEGACY_WAKER=1 ./scripts/start-all-wakers.sh` · [MAP-RUNTIME-WAKER.md](../../../docs/MAP-RUNTIME-WAKER.md)

## 触发方式（仅 legacy runtime-waker：SSE 长连为主路径）

- **主路径**：waker 订阅 `GET /agents/me/notifications/stream`（SSE 长连），由服务端 `notification.created` 帧触发 wake
- **兜底**：`MAP_RUNTIME_INTERVAL`（默认 `600s`，10min）轮询 `map --persona <name> todos` + 未读通知
- **D3 重连补偿**：SSE 断连后指数退避（1s → 30s 上限），重连成功先做一次 `unread_only=true` 全量补漏
- **D4 客户端限速**：同 fingerprint 60s 内最多 1 次 resume 尝试；**补漏事件（`event_source="replay"`）豁免 D4**，服务端 `inbound_event.UNIQUE(fingerprint)` 主闸仍防跨进程重投
- SSE 订阅发生在 waker 进程层（与 backend 无关），三 backend（claude / codex / cursor）等价受益

详见 [MAP-RUNTIME-WAKER.md §SSE long-poll primary path](../../../docs/MAP-RUNTIME-WAKER.md#sse-long-poll-primary-path)。
