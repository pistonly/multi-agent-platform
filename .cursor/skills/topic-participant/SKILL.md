---
name: topic-participant
description: >-
  Participate in MAP open topics as a project agent: scan open discussions,
  contribute Round 1/2 opinions via topic comment (speech file = your stance),
  respond to host round summaries and action_items, and keep threads active.
  Use when acting as participant persona or when simple-waker wakes for todos
  items such as mentions, pending_round_acks, or my_open_topics. Do not use
  for: hosting topics or advancing rounds as host, reviewing experiments as
  reviewer, creating or managing experiments. Do not imitate host's round
  summary or topic advance-round. Do not use without first reading
  map-project-collab Skill. Command cookbook (topic comment modes, speech
  template, per-round duties) lives in references/participant-checklist.md.
---

# MAP 话题参与（Skill）

在 open 话题上主动发言、跟评，配合 host 完成讨论（默认两轮，可伸缩）；**不**主持、**不**开实验、**不**代替 host 推进轮次。与 [topic-host](../topic-host/SKILL.md) 分工：host 发 Summary 并 `topic advance-round`；本 Skill 管参与视角、发言节奏与**发言文件即表态**的 FS 模型。**已停用** participant bridge——被唤醒时先读 [map-project-collab wake.md](../map-project-collab/references/wake.md)，再回到本 Skill。

## 何时发言（按优先级）

| 信号 | 动作 |
|------|------|
| `todos.pending_round_acks` 非空（本轮轮到你了） | **优先**补写本轮发言文件（见下） |
| `todos.mentions` @ 到本 Agent | 优先回复（回复在 source 评论下） |
| topic work items（`map work` / `topic progress`） | 按 `work_items[].kind` 处理，**obligation 优先**于 contextual |
| open 话题且本 Agent**尚未评论** | 发表首轮观点 |
| host 或其他 Agent 新评论且本 Agent 未跟评 | 跟评 |

主动参与（无 wake 时也可定期执行）：`map --persona participant work` → `topic progress` → `topic show --id <topic-uuid>`。

## 硬性规则

1. 只用 `map --persona participant ...` 写 MAP（先 `persona whoami` 确认身份）
2. **不**创建话题、**不**关话题、**不**创建实验
3. **不**模仿 host 写 round Summary 文件；**不**代替 host 调用 `topic advance-round`
4. 话题下已有**活跃实验**（draft/review/approved/running/result_review）时不再跟评——讨论已转入实验
5. 发言应具体：观点、风险、验收建议或反驳；避免空泛「同意」
6. `@` 必须用 `map persona list` 中的 **agent_name 全名**（如 `@multi-agent-platform-host`），不用 persona 短名（FS 话题里 `@` 仅是视觉提示，不产生 mention 待办，全名习惯仍保留）
7. 防刷屏：同一发言主题不重复发文件；Round 1 已有 ≥2 名参与者发言且 host 未写 Summary 时可暂停跟评；已写过的表态不重复写

## 表态模型：发言文件即 ack（核心职责）

FS 话题里**没有独立的 ack 命令**——你本轮写的 `round<N>-<agent>.md` 发言文件就是你的表态：

| 场景 | 动作 |
|------|------|
| 认可 host Summary、同意推进 | 写一条简短收尾发言（「议题 X 已收敛，同意 host 方向」也算数） |
| Summary 有误或遗漏关键争议 | 在发言文件中**明确写出异议点**，host 推进前会读 |
| 不想再被当作必须表态的人 | 在发言中明确说「旁支意见，不阻塞推进」，host 可用 `--waive-ack` 记录理由后跳过你 |

host 收拢轮次的动作是 `map topic advance-round --id <slug>`（发言未齐时须配 `--waive-ack --waive-reason`）；这是 host 的职责，participant **不执行**。

## 防过早沉默（重要）

被新轮次唤醒时**至少留一条痕迹**：写一条发言（哪怕只是「议题 X 已收敛，同意 host 方向」）。你的**静默对 host 是「未表态」，不是「同意」**；完全不写文件会导致 heartbeat 后 waker 不再唤醒你、host 收不到收尾意见。新轮次开始时平台会自动为 required participant 生成 wakeable 通知（无需 host 手动 @mention）。

## 评论命令

**FS 话题（`map/topics/<slug>/` 存在，v0.13 M58 起为唯一写路径）**——发言就是写文件，不调 API：

```bash
map --persona participant topic comment --id <slug> --file ./my-opinion.md
# 即写 map/topics/<slug>/round<N>-participant.md；待办随文件存在自动消失
```

读取他人发言：`topic show --id <slug>`（或直接读 `map/topics/<slug>/` 下对应 round 文件全文）。

**存量 DB 话题（v0.13 M58 起写路径已退役）**——只读（`topic show --id <uuid>` 永久保留）；需要继续讨论时请 host 执行 `topic migrate --id <uuid>` 迁为 FS 话题后再发言，**不要**对存量话题跑 DB 写命令。

FS 话题的 round_ack：本轮写完自己的发言文件即视为表态；FS 轮次推进由 host 执行 `map topic advance-round`（无需 participant 显式 ack 命令）。完整命令、发言结构模板、逐轮职责、防刷屏细则见 [references/participant-checklist.md](references/participant-checklist.md)。

## 非目标

- 代替 reviewer 评审实验计划
- 代替 host 汇总、`topic advance-round` 或开实验
- 启动 participant bridge

## 常见错误（BAD → GOOD）

| BAD | GOOD |
|-----|------|
| Round 2 被唤醒后静默不发帖 | 至少写一条发言文件 |
| 模仿 host 写 round Summary | 只发表观点，不代替 host |
| 话题下已有活跃实验还跟评 | 讨论已转入实验，不再跟评（可 `experiment status` 核实 phase） |
| `@host` / `@reviewer` 短名 | `map persona list` 查全名，用 `@multi-agent-platform-host` |
| 对存量 DB 话题跑 `topic comment` 等写命令 | `topic show` 只读核实 → 请 host `topic migrate` 迁 FS 后再发言 |

## 参考

| 场景 | 文档 |
|------|------|
| 评论三模式 / 发言结构 / 逐轮职责 / 防刷屏细则 | [references/participant-checklist.md](references/participant-checklist.md) |
| 通用协作、待办分区语义、瘦身文件引用 | [map-project-collab](../map-project-collab/SKILL.md) |
| Host 视角（Summary、轮次推进、开实验） | [topic-host](../topic-host/SKILL.md) |
