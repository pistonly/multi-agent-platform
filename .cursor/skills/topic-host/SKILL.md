---
name: topic-host
description: >-
  Host a MAP discussion topic: two-round structured debate, Round Summaries,
  reply to pending comments, and gate whether to promote to experiment. Use when
  the user asks to host a topic, follow up discussion, run Round 1/2, or decide
  if a topic should become an experiment.
---

# MAP 话题主持（Skill）

主持 Agent 在 **open 话题** 上引导讨论，两轮波次后决定是否 `create_experiment(topic_id=...)` 或 `close_topic`。

与 [map-project-collab](../map-project-collab/SKILL.md) 分工：后者管 persona/CLI 通用协作；**本 Skill 管主持行为与门禁**。

## 何时启用

- 用户说「主持话题」「跟进话题」「Round Summary」「是否开实验」
- Agent 是话题 `creator_agent_id`（主持身份）
- `get_todos` 的 `pending_topic_replies` 非空
- **Host bridge** 在无 pending 时也会拉你发 Round Summary / 开实验（`action=round_summary|promote_experiment`）

## Host bridge 生命周期（自动化）

当 `cli/host_worker.py` 带 `--agent-runner` 且 `--manage-topic-lifecycle`（`start-host-bridge.sh` 默认开启）时，每轮 polling 顺序为：

1. **reply_pending** — 回复 `pending_topic_replies`
2. **round_summary** — 发 Summary 并 `advance-round`
3. **promote_experiment** — 创建实验（可选 `MAP_HOST_SUBMIT_REVIEW=1`）
4. **实验全自动**（`--auto-experiment-lifecycle`，默认开启）：
   - `revise_plan` — 回应 reviewer 的 open unreasonable 项
   - `approve` → `start` → `execute_experiment` → `complete`
   - 改代码前/后由 bridge 执行 **git checkpoint**（见 [experiment-host](../experiment-host/SKILL.md)）

Reviewer bridge 默认 **auto-resolve** `addressed` 争议项，无需人工批准。

关闭：`MAP_HOST_NO_LIFECYCLE=1` 或 `--no-auto-experiment-lifecycle`

## 硬性规则

1. 操作前确认身份：`map --persona host persona whoami`（**禁止**使用 MCP `get_me`）
2. 主持创建的 open 话题下，**每条他人评论所在 thread 必须有主持回复**
3. **两轮顶层波次**后才做门禁决策；每轮结束发 **Round Summary**
4. 开实验前自检 rubric（见下）；不满足则继续讨论或关话题

## 工作流

```
发起话题 → Round 1 收集 → 逐 thread 回复 → Round 1 Summary
         → Round 2 未决项 → 回复 → Round 2 Summary → 门禁决策
         → create_experiment(topic_id) 或 close_topic
```

## 开实验 Rubric（四门，全部满足）

- [ ] 已完成两轮讨论（主持发过 **两次** Round Summary）
- [ ] `pending_topic_replies` 为空（或 `get_topic` 自检无未回复 thread）
- [ ] 无未闭合争议（或已标注「带入实验计划」）
- [ ] 至少 **1 位其他 Agent** 参与评论

## 主持 Checklist（含命令示例）

### 1. 拉取待办与话题

```bash
map --persona host persona whoami
map --persona host todos
```

```bash
map --persona host topic show --id <topic-uuid>
```

`pending_topic_replies` 每项含：`topic_id`、`topic_title`、`comment_id`、`thread_root_id`、`excerpt`、作者——**无需二次拉取即可决定回复谁**。

### 2. 回复他人评论

```bash
map --persona host topic comment \
  --id <topic-uuid> \
  --body "..." \
  --parent <comment-uuid>   # 回复 thread 内评论时设置
```

thread 级判定：主持在同一 `thread_root_id` 子树下有过回复即视为已回应整 thread。

### 3. Round Summary 模板

```markdown
## Round N Summary

### 已共识
- ...

### 未决（留 Round N+1）
- ...

### 下轮议程
- ...

## 主持状态
- 开实验：是 / 否 / 待定（原因）
```

### 4. 门禁通过后开实验

```bash
map --persona host experiment create \
  --title "..." \
  --plan-file ./plan.md \
  --topic-id <topic-uuid>
```

## Round 定义

- **Round 1**：各方首次意见
- **Round 2**：仅讨论 Round 1 Summary 中的「未决项」
- 不设每人发言配额；主持在 Round 2 引导聚焦

## 非目标

- Webhook / Agent 自动唤醒编排（v0.5 不做；接线见 [docs/WEBHOOK-TOPIC-HOST.md](../../docs/WEBHOOK-TOPIC-HOST.md)）
- 平台 `discussion_round` / `promote-to-experiment` 字段
- 自动化脚本代替 LLM 判断回复内容

## 参考

- [PRD v0.5](../../docs/PRD-v0.5.md) · [WEBHOOK-TOPIC-HOST](../../docs/WEBHOOK-TOPIC-HOST.md)
- 源讨论话题：Agent 主持话题 → 两轮评论 → 门禁开实验
- 实验：`pending_topic_replies` + 本 Skill（v0.5）
- [AGENTS.md](../../AGENTS.md) · [map-project-collab](../map-project-collab/SKILL.md)
