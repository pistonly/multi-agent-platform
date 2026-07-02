---
name: map-runtime-waker
description: >-
  Thin bootstrap when an Agent Runtime is resumed by the map-runtime-waker daemon.
  Read this first on wake, then map-project-collab and the persona skill. Not for
  manual Cursor chat — use map-project-collab instead.
---

# MAP Runtime Waker（调度壳）

被 **map-runtime-waker** 守护进程唤醒时读本文。**CLI 规则、业务细节不在此重复**——以 [map-project-collab](../map-project-collab/SKILL.md) 与 persona Skill 为唯一行为源。

手动协作（用户在 Cursor 发指令）**不需要**读本文，直接用 `map-project-collab` + persona Skill。

## 每次 wake 的顺序

1. [map-project-collab](../map-project-collab/SKILL.md) — persona、CLI 硬性规则
2. 下表 persona Skill — 具体怎么做
3. `map --persona <persona> persona whoami` → `map --persona <persona> todos`
4. **必须**按 wake hint 的 `kind`（= Web UI 待办分区名）处理对应项
5. 做**一步**可验证推进；**必须**让该项从 `map todos` 或通知列表消失后再收尾

## 核心规则（与 Web UI 一致）

| 规则 | 说明 |
|------|------|
| todos 即真相 | waker 只根据 `GET /agents/me/todos` + 未读通知 wake；**kind 名 = todos 字段名** |
| 清理 = 与 UI 相同 | 处理完成后调用与 UI 等价的 API（见下表）；**禁止**凭 session 记忆判断「已处理」 |
| 一步一 wake | 一次 wake 只推进当前 todo 项的下一步 |
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

`./scripts/start-all-wakers.sh` · 后端与凭证见 [MAP-RUNTIME-WAKER.md](../../../docs/MAP-RUNTIME-WAKER.md)
