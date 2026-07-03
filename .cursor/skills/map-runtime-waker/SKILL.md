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
| **runtime-waker**（默认生产） | `./scripts/start-all-wakers.sh` | 一步一 wake：一次只推进当前 kind 对应的一项 |
| **simple-waker**（简化版） | `./scripts/start-all-simple-wakers.sh` | 批量：一次提醒内处理所有当前待办，直到 `todos` 清空或明确 blocker |

**simple-waker** 下忽略 wake hint 里的单项 `kind`，以 `map todos` 全量为准自主排序与批处理。其余规则（todos 即真相、清理 = 与 UI 相同）不变。

## 每次 wake / 提醒 的顺序

1. [map-project-collab](../map-project-collab/SKILL.md) — persona、CLI 硬性规则
2. 下表 persona Skill — 具体怎么做
3. `map --persona <persona> persona whoami` → `map --persona <persona> todos`
4. **runtime-waker**：按 wake hint 的 `kind` 处理对应一项，做一步可验证推进
5. **simple-waker**：处理所有当前待办（可批量），以 `todos` 为空或每项有明确处置为准
6. **必须**让已处理项从 `map todos` 或通知列表消失后再收尾

## 核心规则（与 Web UI 一致）

| 规则 | 说明 |
|------|------|
| todos 即真相 | waker 只根据 `GET /agents/me/todos` + 未读通知 wake；**kind 名 = todos 字段名** |
| 清理 = 与 UI 相同 | 处理完成后调用与 UI 等价的 API（见下表）；**禁止**凭 session 记忆判断「已处理」 |
| 一步一 wake | 仅 **runtime-waker**：一次 wake 只推进当前 todo 项的下一步 |
| 批量提醒 | 仅 **simple-waker**：一次提醒可处理多项；收尾前再跑 `todos` 验证 |
| skip ≠ 执行中 | `wake_skips` 表示 heartbeat/TTL 内已 wake 过（去重），不是后台在跑 |
| heartbeat | 待办项仍在 API 中时，TTL 到期后会重 wake（自愈） |

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

- runtime-waker：`./scripts/start-all-wakers.sh` · [MAP-RUNTIME-WAKER.md](../../../docs/MAP-RUNTIME-WAKER.md)
- simple-waker：`./scripts/start-all-simple-wakers.sh` · [MAP-SIMPLE-WAKER.md](../../../docs/MAP-SIMPLE-WAKER.md)

## 触发方式（v0.8 起 SSE 长连为主路径）

- **主路径**：waker 订阅 `GET /agents/me/notifications/stream`（SSE 长连），由服务端 `notification.created` 帧触发 wake
- **兜底**：`MAP_RUNTIME_INTERVAL`（默认 `600s`，10min）轮询 `map --persona <name> todos` + 未读通知
- **D3 重连补偿**：SSE 断连后指数退避（1s → 30s 上限），重连成功先做一次 `unread_only=true` 全量补漏
- **D4 客户端限速**：同 fingerprint 60s 内最多 1 次 resume 尝试；**补漏事件（`event_source="replay"`）豁免 D4**，服务端 `inbound_event.UNIQUE(fingerprint)` 主闸仍防跨进程重投
- SSE 订阅发生在 waker 进程层（与 backend 无关），三 backend（claude / codex / cursor）等价受益

详见 [MAP-RUNTIME-WAKER.md §SSE long-poll primary path](../../../docs/MAP-RUNTIME-WAKER.md#sse-long-poll-primary-path)。
