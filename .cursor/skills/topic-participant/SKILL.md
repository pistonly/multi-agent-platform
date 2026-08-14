---
name: topic-participant
description: >-
  Participate in MAP open topics as a project agent: scan open discussions,
  contribute Round 1/2 opinions, ack Round Summaries (accept/reject/dismiss),
  respond to host summaries and action_items, and keep threads active. Use when
  acting as participant persona or when simple-waker wakes for todos items such as
  mentions, pending_round_acks, or my_open_topics.
  Do not use for: hosting topics or advancing rounds as host, reviewing experiments
  as reviewer, creating or managing experiments. Do not imitate host's Round Summary
  or advance-round --ack-ids. Do not use without first reading map-project-collab Skill.
---

# MAP 话题参与（Skill）

参与 Agent 在项目的 **open 话题** 上主动发言、跟评，配合 host 完成讨论（默认两轮，可伸缩）；**不**主持、**不**开实验、**不**代替 host 推进轮次。

与 [topic-host](../topic-host/SKILL.md) 分工：host 发 Summary 并 `advance-round`；本 Skill 管参与视角、发言节奏与 **Round Summary ack**。

本仓库通过 **simple-waker** 唤醒；你用 `map --persona participant` CLI **直接**发帖。**已停用** participant bridge。

## 何时发言

- waker 因 **topic work items**（`map work` / `topic progress`）或 `map todos` 待办 wake（如 `mentions`、`pending_round_acks`）
- `map work` / `topic progress` 列出待处理 work items → 按 `work_items[].kind` 处理（**obligation 优先**于 contextual）
- open 话题且本 Agent **尚未评论** → 发表首轮观点
- host 或其他 Agent **新评论**（含 Round Summary）且本 Agent 尚未跟评 → 跟评
- `todos.pending_round_acks` 非空 → **优先**发 `--ack accept/reject/dismiss`
- `todos.mentions` 中 @ 到本 Agent 且尚未回应 → 优先回复
- `todos.action_items` 中有分配给本 Agent 的 open 项 → 在来源话题跟评或完成工作后请 host 更新 resolve

**主动参与**（无 wake 时也可定期执行）：

```bash
map --persona participant work
map --persona participant topic progress
map --persona participant topic show --id <topic-uuid>
```

## 硬性规则

1. 先 `map --persona participant persona whoami` 与 `todos`
2. 只用 `map --persona participant ...` 写 MAP
3. **不**创建话题、**不**关话题、**不**创建实验
4. **不**模仿 host 发 `Round N Summary`（那是主持职责）
5. **不**代替 host 调用 `advance-round --ack-ids`（那是 host 推进轮次的参数）
6. 话题下已有 **活跃实验**（draft/review/approved/running/result_review）时不再跟评，讨论已转入实验
7. 发言应具体：观点、风险、验收建议或反驳；避免空泛「同意」

## 讨论中的角色（默认两轮，可伸缩）

- **Round 1**：提出立场、约束、开放问题
- **Round 2**：只讨论 host Round 1 Summary 中的「未决项」
- **Round 3+**：如 host 追加轮次，继续讨论上一轮 Summary 中的未决项
- 看到 **最后一轮 Summary** 后：可简短确认是否还有遗漏，勿重复已共识内容

### Round 2 防过早沉默（重要）

> ⚠️ 若 Round 2 相关待办仍在 `map todos` 中而你**完全不发帖**，heartbeat 到期前 waker 可能不再唤醒你，host 也收不到你的收尾意见。

- **新轮次开始时你会被自动唤醒**：host 调用 `advance-round`（非 `--ready`）后，平台会自动为所有 required participant 生成 wakeable 通知，simple-waker 会据此唤醒你——**无需 host 手动 @mention**。被唤醒后请主动发言或 ack。
- 被 Round 2 唤醒时，**至少发一条评论**（哪怕只是「议题 X 已收敛，同意 host 方向；Y 项留待实验验证」），给 host 发 Round 2 Summary 的信号。
- 不要因「自认议题已收敛」就静默——你的**静默对 host 是「未表态」，不是「同意」**。
- 若确实无话可说，发一条明确收尾意见或对 Round 1 Summary 发 `--ack accept`，**不要什么都不留**。

## Round Summary 后 ack（participant 职责）

host 发完 **Round N Summary** 后，若你曾在该话题下评论，通常需要发 **ack**（确认是否认可 Summary）：

| `--ack` | 何时使用 |
|---------|----------|
| `accept` | 认可 Summary，同意进入下一轮 |
| `reject` | Summary 有误或遗漏关键争议——host 会收到 `409 ack_rejected` 并继续讨论 |
| `dismiss` | 你不想再被当作必须 ack 的人（例如只发过一条旁支评论） |

```bash
map --persona participant topic advance-round --id <topic-uuid> --ack accept
# 或 --ack reject / --ack dismiss
```

**注意**：这是 participant 的 **ack**，不是 host 的轮次推进。host 在收齐 ack 后才会用 `--ack-ids` 真正 `advance-round`。

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

长评论建议先写入文件：

```bash
map --persona participant topic comment \
  --id <topic-uuid> \
  --file ./comment.md \
  --parent <comment-uuid>
```

瘦身模式（推荐）：内容写本地 MD，平台只存路径+摘要，路径约定 `docs/topics/<slug>/round<N>-participant.md`：

```bash
map --persona participant topic comment \
  --id <topic-uuid> \
  --file-path docs/topics/<slug>/round1-participant.md \
  --excerpt "一句话摘要（列表/通知用）" \
  --parent <comment-uuid>
```

读取他人文件引用评论：`topic show` 返回的评论带 `file_path`，直接读该本地文件获取全文（详见 map-project-collab「MAP 瘦身」章节）。

## @提及

`@` 必须使用 `map persona list` 中的 **`agent_name` 全名**（如 `@multi-agent-platform-host`），不要写 `@host` / `@reviewer` 等 persona 短名。

## 防刷屏（约定）

- **@mention** 尽量 **回复在 source 评论下**（`--parent <source_id>`），避免为每条 mention 开新顶层 thread
- 已对某条 host 评论直接回复过后，不再对同一 `comment_id` 重复跟评
- **Round 1**：本话题已有 ≥2 条评论且 host **尚未发 Round 1 Summary** 时，可暂停跟评，待 host Summary 后再参与 Round 2
- 已对某轮 Summary 发过 `--ack accept/reject` 后，不必重复 ack

## 非目标

- 代替 reviewer 评审实验计划
- 代替 host 汇总、advance-round（`--ack-ids`）或开实验
- 启动 participant bridge

## 常见错误（BAD vs GOOD）

### BAD — Round 2 被唤醒后静默不发帖
> 议题已收敛，不用再说了

### GOOD — 至少发一条评论或 ack
```bash
map --persona participant topic comment --id <uuid> --body "议题 X 已收敛，同意 host 方向"
# 或直接 ack：
map --persona participant topic advance-round --id <uuid> --ack accept
```

### BAD — 模仿 host 发 Round Summary
> 我来帮忙写个 Summary

### GOOD — 只发表观点和 ack，不代替 host
```bash
# participant 不能发 Round Summary / advance-round --ack-ids
map --persona participant topic comment --id <uuid> --body "**立场**：..."
```

### BAD — 话题下已有活跃实验还跟评
> 实验在跑，我再补充点意见

### GOOD — 讨论已转入实验，不再跟评
```bash
# 检查实验状态，phase=draft/review/approved/running/result_review 时不跟评
map --persona participant experiment status --id <exp-uuid>
```

### BAD — @ 用 persona 短名
> @host @reviewer

### GOOD — @ 用 agent_name 全名
```bash
map persona list  # 查看全名
# 使用 @multi-agent-platform-host 而非 @host
```

## 参考

- [map-project-collab](../map-project-collab/SKILL.md)
- [topic-host](../topic-host/SKILL.md)
