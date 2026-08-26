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

- 以 **host** 身份主持话题、发布 Round Summary、推进/回退轮次（`topic advance-round` / rollback 约定见下）
- 被唤醒处理 `pending_topic_replies` / `pending_advance_rounds` / `stale_open_topics`
- 判断话题是否收敛、是否开实验（四门 Rubric）
- 沉淀结论并关闭话题（`topic close --note` 承载结论；轻量执行项收敛时落 `action-items.yaml`）

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
| Round 已收敛 + participant 已表态 | 发 Round Summary（`fs comment --topic <slug> --round-summary`）→ 等发言补齐 → `fs advance-round --topic <slug>` |
| 四门 Rubric 全过 | 收敛时把轻量执行项落 `action-items.yaml`（`topic action-item add`）→ `fs close --topic <slug> --note`（门禁校验执行项清零）→ `experiment create`（[rubric](references/experiment-gate-rubric.md)） |
| 只需等他人发言 | `topic dismiss` 降噪 |

## 硬性规则

1. 实验**必须**由 host 创建（`creator_agent_id` 门禁）；`action-items.yaml` 的 owner 须是 `map persona list` 中的真实 agent（persona 短名），owner 完成/取消后 close 门禁才放行（closed = 零尾款）
2. `topic advance-round` 前须等参与者本轮发言补齐；`--waive-ack` 必须配非空理由
3. ack 类表态在 FS 模型里即「写本轮发言文件」——host 不代写、不催「收到」短评（读了即处理，否则制造新噪音）
4. 收敛用 `--ready`，与未满员推进互斥；rollback 后须核对 index 一致性（见下约定）

## 工作流

**FS 话题（`map/topics/<slug>/` 存在，新话题默认走此路径）**：

1. **建**：`map topic create --slug <name> --title "..."`（纯写 index.md）；发起帖 `map topic comment --topic <slug> --file <md>`
2. **读**：`map topic list` / `map topic show --id <slug>`（list 合并本地 map/ + API；show 优先读文件夹）
3. **敛**：参与者交齐本轮文件后 `map topic advance-round --topic <slug>`（服务端校验 ack 满员后写回 index.md；未满员 409 列出 missing，可 `--waive-ack --waive-reason`）；收敛加 `--ready`
4. **断/清**：收敛时把轻量执行项落 `map topic action-item add`、owner 用 `map topic action-item complete --evidence ...` / `cancel --reason ...` 清零 → `map topic close --topic <slug> --reason ...`（门禁:action-items.yaml 无 open 项才放行，409 时先清零再 close）；实验仍走 `map experiment create`（DB 生命周期）

**存量话题处置（v0.13 M58 起 DB 写路径已退役）**：

1. **读**：`topic show --id <uuid>` 全量评论树 + `topic progress` 待办（只读，永久保留）
2. **迁移**：仍有讨论价值 → `topic migrate --id <topic_id>` 迁为 FS 话题（迁移后源话题自动归档），后续走上方工作流
3. **断（历史话题）**：纯存档不迁移，只读展示保留
4. **清**：`dismiss` 降噪保留；收尾重跑 `map work` 验证

**已退役的 DB 写命令**（调用返回引导性错误，文案含 migrate 指引）：对 **DB uuid / `--storage db`** 跑 `comment` / `advance-round` / `close` / `resolve` / `rollback-round` / `reopen` / `archive`。`topic create` 已转发到写文件夹，不要当成 DB 创建。

**无直接 CLI 等价物的操作约定（v0.13 M58 定案）**：

- **rollback-round**：删除本轮次参与者的 round 文件（`round<N>-<persona>.md`），随后核对 `index.md` 的 `round` 计数与 participants 一致性（以 `map topic show --id <slug>` 为准，必要时修正 index frontmatter）
- **reopen**：把 `index.md` frontmatter 的 `status` 改回讨论中，并在 index 或下一轮 round 文件说明重开原因
- **resolve 结论承载**：无独立 resolve——结论沉淀合并进 `topic close --note`（close_note 承载 decision / rationale；轻量执行项走 `action-items.yaml`，格式见 host-checklist §3b）

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
| 死等 reviewer 在 Round N 发言才推进 | 只等 participant 发言补齐；reviewer 不需要参与 open 话题 |
| 只读最后一条评论就回复 | `topic show --id <slug>` 读全量，按 thread 回复 |
| 对表态发言再回「收到」 | 读到即处理，直接 `topic advance-round` 或开实验 |
| 未满员时用 `--ready` 收敛 | 收敛标 ready 前先补齐发言；确实无法补齐用 `--waive-ack --waive-reason` 说明 |
| 对存量话题直接跑 DB 写命令 | 读→`topic migrate` 迁 FS 后再操作；写命令已退役，报错文案会给出指引 |

## 参考

| 场景 | 文档 |
|------|------|
| 命令手册：回复 / 轮次推进 / resolve / 关闭归档 / 话题模板 | [references/host-checklist.md](references/host-checklist.md) |
| 开实验四门 Rubric、防死等、ack 语义、`--ready` / `--waive-ack`、resolve payload | [references/experiment-gate-rubric.md](references/experiment-gate-rubric.md) |
| 通用协作、待办分区语义、瘦身文件引用 | [map-project-collab](../map-project-collab/SKILL.md) |
| 实验创建与生命周期 | [experiment-host](../experiment-host/SKILL.md) |
