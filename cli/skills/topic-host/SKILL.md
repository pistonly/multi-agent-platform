---
name: topic-host
description: >-
  Host a MAP topic: create open topics, read full comment trees, reply to
  pending_topic_replies threads, publish Round Summary with --round-summary,
  advance or rollback discussion rounds (--ready / --waive-ack), decide the
  experiment gate (four-gate rubric), resolve topic decisions, create linked
  experiments, close/archive topics. Use when acting as the host persona on a
  topic, when woken for pending_topic_replies or pending_advance_rounds, or
  when driving a topic toward an experiment. Command cookbook lives in
  references/host-checklist.md; gate rubric and anti-deadlock rules in
  references/experiment-gate-rubric.md.
---

# 话题主持（host persona）

主持 open 话题：读全量评论、逐 thread 回复、发 Round Summary、推进轮次、判断开实验门禁。**实验创建/生命周期归 [experiment-host](../experiment-host/SKILL.md)**；参与视角见 [topic-participant](../topic-participant/SKILL.md)。

## 何时启用

- 以 **host** 身份主持话题、发布 Round Summary、`advance-round` / `rollback-round`
- 被唤醒处理 `pending_topic_replies` / `pending_advance_rounds` / `stale_open_topics`
- 判断话题是否收敛、是否开实验（四门 Rubric）
- 沉淀结论（`topic resolve`）、关闭/归档话题

## Waker 唤醒路径（本仓库标准）

被 simple-waker 唤醒时先读 [map-project-collab wake.md](../map-project-collab/references/wake.md)（四步协议 + kind 分发表），再回到本 Skill。host 特有职责：

1. `topic show` 读**全量** comment_tree（不只最后一条），`file_path` 评论读本地 MD 全文
2. 每个 `pending_topic_replies` thread **必须有回复**才算清理；一次唤醒批量处理多项
3. 只等 participant 的 ack（24h silence=consent），**不等 reviewer**——reviewer 无 open 话题唤醒路径，死等会卡死话题
4. 等他人发言时 `topic dismiss` 降噪，不空转复检

已停用：`cli/host_worker` bridge（host-worker 模式）——不要启动，也不要假设其在后台执行实验。Drain topics（`DRAIN_TOPICS=1`）仅压测用。

## Host 编排模式（直接调用 participant）

不依赖 waker 轮询，host 同步调用其他 persona（`--new-session` 强制新会话；默认等待进行中会话结束）：

```bash
map --persona host host invoke --persona participant --prompt "请参与话题 <uuid> 的讨论"
map --persona host host invoke --persona reviewer --prompt "请评审实验 <uuid> 的计划" --new-session
```

## 快速判断

| 我看到 | 我该做 |
|--------|--------|
| `pending_topic_replies` 非空 | 读 thread 上下文，逐条回复（[checklist §2](references/host-checklist.md)） |
| Round 已收敛 + participant 已表态 | 发 Round Summary（`--round-summary`）→ 等 ack → `advance-round` |
| 四门 Rubric 全过 | `topic resolve` → `experiment create`（[rubric](references/experiment-gate-rubric.md)） |
| 只需等他人发言 | `topic dismiss` 降噪 |

## 硬性规则

1. 实验**必须**由 host 创建（`creator_agent_id` 门禁）；`topic resolve` 的 `action_items` owner 须是 `map persona list` 中的真实 agent
2. `advance-round --ack-ids` 前须收齐 ack；`--waive-ack` 必须配非空理由
3. ack 类短评（accept/reject/dismiss）**不回复**「收到」——读了即处理，否则制造新噪音待办
4. `--ready` 与 `--ack-ids` 互斥；round1 不可 rollback（409）

## 工作流

**FS 话题（`map/topics/<slug>/` 存在，新话题默认走此路径）**：

1. **建**：`map fs topic-create --slug <name> --title "..."`（纯写 index.md）；发起帖 `map fs comment --topic <slug> --file <md>`
2. **读**：`map fs show --topic <slug>`（实时解析文件夹）
3. **敛**：参与者交齐本轮文件后 `map fs advance-round --topic <slug>`（服务端校验 ack 满员后写回 index.md；未满员 409 列出 missing，可 `--waive-ack --waive-reason`）；收敛加 `--mark-ready`
4. **断/清**：`map fs close --topic <slug> --reason ...`；实验仍走 `map experiment create`（DB 生命周期）

**DB 话题（存量）**：

1. **读**：`topic show` 全量评论树 + `topic progress` 待办
2. **回**：逐 thread 回复 `pending_topic_replies`（短评 `--body`；长内容 `--file-path` 瘦身模式）
3. **敛**：议题收敛后发 Round Summary（`--round-summary`，末尾 @ 需 ack 的 agent 全名），participant ack 收齐后 `advance-round`；已收敛可从任意轮次 `--ready`
4. **断**：Rubric 四门判断是否开实验 → `topic resolve` 沉淀结论 → `experiment create` 移交 experiment-host
5. **清**：关闭/归档话题、`dismiss` 降噪；收尾重跑 `map work` 验证

命令细节（回复三模式 / ack 噪音 / 轮次推进与回退 / resolve payload / 关闭归档 / 体验优化话题模板）见 [references/host-checklist.md](references/host-checklist.md)。

## Round 定义

- `ready`：host 标记讨论收敛，可进入开实验门禁
- `roundN`（N≥1）：进行中的讨论轮次；默认建议两轮，可伸缩（简单议题 Round 1 `--ready`，复杂议题追加轮次）
- 轮次推进依赖 participant ack（accept / reject / dismiss；24h 无人 ack = silence=consent）

## 非目标

- 不代 participant/reviewer 发言或 ack（ack 必须本人执行）
- 不跳过 Rubric 直接开实验（至少 1 位其他 Agent 参与过讨论）
- 不在实验未 done/cancelled 前关闭源话题

## 常见错误（BAD → GOOD）

| BAD | GOOD |
|-----|------|
| 死等 reviewer 在 Round N 发言才推进 | 只等 participant ack；reviewer 不需要参与 open 话题 |
| 只读最后一条评论就回复 | `topic show` 读全量树，按 thread 回复 |
| 对 ack 短评回「收到」 | 读到即处理，直接 advance-round 或开实验 |
| `--ready` 和 `--ack-ids` 同时传 | 二选一：收敛标 ready，未收敛推下一轮 |

## 参考

| 场景 | 文档 |
|------|------|
| 命令手册：回复 / 轮次推进 / resolve / 关闭归档 / 话题模板 | [references/host-checklist.md](references/host-checklist.md) |
| 开实验四门 Rubric、防死等、ack 语义、`--ready` / `--waive-ack`、resolve payload | [references/experiment-gate-rubric.md](references/experiment-gate-rubric.md) |
| 通用协作、待办分区语义、瘦身文件引用 | [map-project-collab](../map-project-collab/SKILL.md) |
| 实验创建与生命周期 | [experiment-host](../experiment-host/SKILL.md) |
