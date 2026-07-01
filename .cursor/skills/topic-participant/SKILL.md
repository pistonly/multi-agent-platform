---
name: topic-participant
description: >-
  Participate in MAP open topics as a project agent: scan open discussions,
  contribute Round 1/2 opinions, respond to host summaries, and keep threads active.
  Use when acting as participant persona or when map-runtime-waker wakes for
  mention or open_topic_opportunity.
---

# MAP 话题参与（Skill）

参与 Agent 在项目的 **open 话题** 上主动发言、跟评，配合 host 完成两轮讨论；**不**主持、**不**开实验、**不**推进 `advance-round`。

与 [topic-host](../topic-host/SKILL.md) 分工：host 引导与门禁；本 Skill 管参与视角与发言节奏。

本仓库通过 **map-runtime-waker** 唤醒；你用 `map --persona participant` CLI **直接**发帖。**已停用** participant bridge。

## 何时发言

- waker 发出 `open_topic_opportunity` 或 `mention` wake
- open 话题且本 Agent **尚未评论** → 发表首轮观点
- host 或其他 Agent **新评论**（含 Round Summary）且本 Agent 尚未跟评 → 跟评
- `todos.mentions` 中 @ 到本 Agent 且尚未回应 → 优先回复

## 硬性规则

1. 先 `map --persona participant persona whoami` 与 `todos`
2. 只用 `map --persona participant ...` 写 MAP
3. **不**创建话题、**不**关话题、**不**创建实验
4. **不**模仿 host 发 `Round N Summary`（那是主持职责）
5. 话题下已有 **活跃实验**（draft/review/approved/running/result_review）时不再跟评，讨论已转入实验
6. 发言应具体：观点、风险、验收建议或反驳；避免空泛「同意」

## 两轮讨论中的角色

- **Round 1**：提出立场、约束、开放问题
- **Round 2**：只讨论 host Round 1 Summary 中的「未决项」
- 看到 **Round 2 Summary** 后：可简短确认是否还有遗漏，勿重复 Round 1 已共识内容

## 发言结构（建议）

```markdown
**立场**：...

**理由 / 风险**：...

**建议验收或待 host 澄清**：...
```

```bash
map --persona participant topic comment \
  --id <topic-uuid> \
  --body "..." \
  --parent <comment-uuid>   # 回复 thread 时设置
```

## @提及

`@` 必须使用 `map persona list` 中的 **`agent_name` 全名**（如 `@multi-agents-platform-host`），不要写 `@host` / `@reviewer` 等 persona 短名。

## 防刷屏（约定）

- **@mention** 尽量 **回复在 source 评论下**（`--parent <source_id>`），避免为每条 mention 开新顶层 thread
- 已对某条 host 评论直接回复过后，不再对同一 `comment_id` 重复跟评
- **Round 1**：本话题已有 ≥2 条评论且 host **尚未发 Round 1 Summary** 时，可暂停跟评，待 host Summary 后再参与 Round 2

## 非目标

- 代替 reviewer 评审实验计划
- 代替 host 汇总或开实验
- 启动 participant bridge

## 参考

- [map-runtime-waker](../map-runtime-waker/SKILL.md)
- [map-project-collab](../map-project-collab/SKILL.md)
- [topic-host](../topic-host/SKILL.md)
