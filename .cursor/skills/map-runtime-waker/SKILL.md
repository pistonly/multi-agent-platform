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
4. 按 wake hint 做**一步**；已无待办 → 说明「无需动作」后结束

## 仅本文的 wake 规则

| 规则 | 说明 |
|------|------|
| todos 优先 | prompt 里 `kind` / `object_id` 只是提示；以 `todos` 与对象详情为准 |
| 一步一 wake | 一次 wake 只推进当前事件的下一步 |
| skip ≠ 执行中 | `wake_skips` 表示 fingerprint 在 TTL 内已 wake 过（去重），不是后台在跑实验 |
| woken 自愈 | woken **不再永久去重**：TTL（默认 30min，`--woken-cooldown-seconds`）到期后会被重新唤醒。每次醒都以当前 `todos` 为准**重新判断**，不要假设「上次处理过 = 这次无动作」；若确实无事可做，也要明确收尾 / ack，**别静默**（静默会让 waker 误判已处理而暂停推进） |
| action_items | waker 可能无专用 wake；`todos.action_items` 非空时仍须处理 |
| round_ack_pending | `todos.pending_round_acks` 非空 → participant/reviewer 发 `--ack accept/reject/dismiss` |
| 平台反馈 | 发现 MAP 本身的问题/改进点 → [map-project-collab §平台反馈](../map-project-collab/SKILL.md) 用 `map feedback submit` |

## kind → 读哪个 Skill

| kind | persona | Skill |
|------|---------|-------|
| `pending_topic_reply` | host | [topic-host](../topic-host/SKILL.md) §回复 |
| `topic_lifecycle` | host | [topic-host](../topic-host/SKILL.md) |
| `experiment_lifecycle` | host | [experiment-host](../experiment-host/SKILL.md) |
| `mention` | participant / reviewer | [topic-participant](../topic-participant/SKILL.md) 或 [experiment-reviewer](../experiment-reviewer/SKILL.md) |
| `round_ack_pending` | participant / reviewer | [topic-participant](../topic-participant/SKILL.md) — `topic advance-round --ack accept` |
| `open_topic_opportunity` | participant | [topic-participant](../topic-participant/SKILL.md) |
| `pending_review` | reviewer | [experiment-reviewer](../experiment-reviewer/SKILL.md) |
| `pending_result_review` | reviewer | [experiment-reviewer](../experiment-reviewer/SKILL.md) |
| `addressed_review_item` | reviewer | [experiment-reviewer](../experiment-reviewer/SKILL.md) |

## Persona 默认 Skill

| persona | 必读 |
|---------|------|
| host | [topic-host](../topic-host/SKILL.md) + [experiment-host](../experiment-host/SKILL.md) |
| participant | [topic-participant](../topic-participant/SKILL.md) |
| reviewer | [experiment-reviewer](../experiment-reviewer/SKILL.md) |

## 部署

`./scripts/start-all-wakers.sh` · 后端与凭证见 [MAP-RUNTIME-WAKER.md](../../../docs/MAP-RUNTIME-WAKER.md)
