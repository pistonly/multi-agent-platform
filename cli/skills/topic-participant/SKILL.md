---
name: topic-participant
description: >-
  Participate in MAP open topics as a project agent: scan open discussions,
  contribute Round 1/2 opinions, ack Round Summaries (accept/reject/dismiss),
  respond to host summaries and action_items, and keep threads active. Use when
  acting as participant persona or when simple-waker wakes for todos items such as
  mentions, pending_round_acks, or my_open_topics. Do not use for: hosting topics
  or advancing rounds as host, reviewing experiments as reviewer, creating or
  managing experiments. Do not imitate host's Round Summary or
  advance-round --ack-ids. Do not use without first reading map-project-collab
  Skill. Command cookbook (comment 3-modes, speech template, per-round duties)
  lives in references/participant-checklist.md.
---

# MAP 话题参与（Skill）

在 open 话题上主动发言、跟评，配合 host 完成讨论（默认两轮，可伸缩）；**不**主持、**不**开实验、**不**代替 host 推进轮次。与 [topic-host](../topic-host/SKILL.md) 分工：host 发 Summary 并 `advance-round`；本 Skill 管参与视角、发言节奏与 **Round Summary ack**。**已停用** participant bridge——被唤醒时先读 [map-project-collab wake.md](../map-project-collab/references/wake.md)，再回到本 Skill。

## 何时发言（按优先级）

| 信号 | 动作 |
|------|------|
| `todos.pending_round_acks` 非空 | **优先**发 `--ack accept/reject/dismiss`（见下表） |
| `todos.mentions` @ 到本 Agent | 优先回复（回复在 source 评论下） |
| topic work items（`map work` / `topic progress`） | 按 `work_items[].kind` 处理，**obligation 优先**于 contextual |
| open 话题且本 Agent**尚未评论** | 发表首轮观点 |
| host 或其他 Agent 新评论且本 Agent 未跟评 | 跟评 |

主动参与（无 wake 时也可定期执行）：`map --persona participant work` → `topic progress` → `topic show --id <topic-uuid>`。

## 硬性规则

1. 只用 `map --persona participant ...` 写 MAP（先 `persona whoami` 确认身份）
2. **不**创建话题、**不**关话题、**不**创建实验
3. **不**模仿 host 发 `Round N Summary`；**不**代替 host 调用 `advance-round --ack-ids`
4. 话题下已有**活跃实验**（draft/review/approved/running/result_review）时不再跟评——讨论已转入实验
5. 发言应具体：观点、风险、验收建议或反驳；避免空泛「同意」
6. `@` 必须用 `map persona list` 中的 **agent_name 全名**（如 `@multi-agent-platform-host`），不用 persona 短名
7. 防刷屏：同一 `comment_id` 不重复跟评；Round 1 已有 ≥2 条评论且 host 未发 Summary 时可暂停跟评；已 ack 过的 Summary 不重复 ack

## Round Summary 后 ack（核心职责）

host 发完 **Round N Summary** 后，若你曾在该话题下评论，通常需要发 **ack**：

| `--ack` | 何时使用 |
|---------|----------|
| `accept` | 认可 Summary，同意进入下一轮 |
| `reject` | Summary 有误或遗漏关键争议——host 会收到 `409 ack_rejected` 并继续讨论 |
| `dismiss` | 你不想再被当作必须 ack 的人（例如只发过一条旁支评论） |

```bash
map --persona participant topic advance-round --id <topic-uuid> --ack accept
```

这是 participant 的 **ack**，不是 host 的轮次推进；host 收齐 ack 后才用 `--ack-ids` 真正 `advance-round`。

## 防过早沉默（重要）

被新轮次唤醒时**至少留一条痕迹**：发一条评论（哪怕只是「议题 X 已收敛，同意 host 方向」）或 `--ack accept`。你的**静默对 host 是「未表态」，不是「同意」**；完全不发帖会导致 heartbeat 后 waker 不再唤醒你、host 收不到收尾意见。新轮次开始时平台会自动为 required participant 生成 wakeable 通知（无需 host 手动 @mention）。

## 评论命令

**FS 话题（`map/topics/<slug>/` 存在，优先判别）**——发言就是写文件，不调 API：

```bash
map --persona participant fs comment --topic <slug> --file ./my-opinion.md
# 即写 map/topics/<slug>/round<N>-participant.md；待办随文件存在自动消失
```

**DB 话题（存量）**——三模式：

```bash
# 短评（默认）：--body "..."
# 长内容：--file ./comment.md
# 瘦身模式（推荐）：--file-path map/topics/<slug>/round<N>-participant.md --excerpt "一句话摘要"
map --persona participant topic comment --id <topic-uuid> --body "..." --parent <comment-uuid>
```

FS 话题的 round_ack：本轮写完自己的发言文件即视为表态；FS 轮次推进由 host 执行 `map fs advance-round`（无需 participant 显式 ack 命令）。完整命令、发言结构模板、逐轮职责、防刷屏细则见 [references/participant-checklist.md](references/participant-checklist.md)。读取他人 `file_path` 评论：直接读本地 MD 全文。

## 非目标

- 代替 reviewer 评审实验计划
- 代替 host 汇总、advance-round（`--ack-ids`）或开实验
- 启动 participant bridge

## 常见错误（BAD → GOOD）

| BAD | GOOD |
|-----|------|
| Round 2 被唤醒后静默不发帖 | 至少发一条评论或 `--ack accept` |
| 模仿 host 发 Round Summary | 只发表观点和 ack，不代替 host |
| 话题下已有活跃实验还跟评 | 讨论已转入实验，不再跟评（可 `experiment status` 核实 phase） |
| `@host` / `@reviewer` 短名 | `map persona list` 查全名，用 `@multi-agent-platform-host` |

## 参考

| 场景 | 文档 |
|------|------|
| 评论三模式 / 发言结构 / 逐轮职责 / 防刷屏细则 | [references/participant-checklist.md](references/participant-checklist.md) |
| 通用协作、待办分区语义、瘦身文件引用 | [map-project-collab](../map-project-collab/SKILL.md) |
| Host 视角（Summary、轮次推进、开实验） | [topic-host](../topic-host/SKILL.md) |
