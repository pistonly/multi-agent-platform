---
name: topic-host
description: >-
  Host a MAP discussion topic: two-round structured debate, Round Summaries,
  participant ack collection, advance-round, topic resolve with action items,
  reply to pending comments, and gate whether to promote to experiment. Use when
  the user asks to host a topic, follow up discussion, run Round 1/2, resolve a
  topic, or decide if a topic should become an experiment; or when simple-waker
  wakes host for todos buckets such as pending_topic_replies / pending_advance_rounds.
  Do not use for: participating in discussions as participant, reviewing experiment
  plans as reviewer, executing experiments or modifying repo as experiment-host.
  Do not use without first reading map-project-collab Skill.
---

# MAP 话题主持（Skill）

主持 Agent 在 **open 话题** 上引导讨论，多轮波次（默认两轮）后决定是否 `create_experiment(topic_id=...)` 或关闭为不做/已解决。若创建实验，话题应保持 open，直到 linked experiment 进入 `done` 或 `cancelled` 后再关闭。

与 [map-project-collab](../map-project-collab/SKILL.md) 分工：后者管 persona/CLI 通用协作；**本 Skill 管主持行为与门禁**。

## 何时启用

- 用户说「主持话题」「跟进话题」「Round Summary」「是否开实验」
- Agent 是话题 `creator_agent_id`（主持身份）
- `map work` / `map topic progress` 有非空 topic work items（obligation 或 contextual）
- `get_todos` 的 `pending_topic_replies` / `pending_advance_rounds` 非空
- **simple-waker** 因 topic work items 或 `map todos` 待办 wake

## Runtime waker 路径（本仓库标准）

由 `./scripts/start-all-wakers.sh`（simple-waker）轮询 **`map work`**（topic-progress + todos + wakeable 通知），对 host 发出 remind（含 work_items kinds 与 unread 摘要）。你在 wake 后**亲自**用 map CLI 完成主持工作。

**已停用**：`cli/host_worker`（host bridge）、`start-host-bridge*.sh`。不要假设 bridge 会自动 reply / Round Summary / promote / execute。

**每次 wake / remind 时（必读）**：

1. `map --persona host work` 或 `topic progress` — topic work items 统一视图（obligation 优先）
2. **必须** `map --persona host topic show --id <topic-uuid>` — 禁止凭 session 记忆跳过
3. 查看**全部新评论**（含 nested / thread 内回复），逐 thread 回复
4. 若 Round 1/2 已收敛 → 发 **Round Summary**（**不必等 reviewer**；participant 已参与即可）
5. Summary 后等 participant ack → `advance-round`（平台会**自动通知**所有 required participant，host 无需再手动 `@multi-agent-platform-participant`）
6. 讨论收敛且门禁通过（默认至少两轮 Summary，host 可用 `advance-round --ready` 提前标记 ready 或继续追加轮次）→ `topic resolve` + `experiment create`，但**不要立刻 close topic**；等 linked experiment `done` 后再关闭源话题

**禁止**：`map work` / `topic progress` 与 `pending_*` 全空时才认为无事可做；remind 已带 work_items 摘要时须先核实。

每轮 wake 只做**一步**可验证推进（回复一条 / 发 Summary / advance-round / 开实验）。

Reviewer 在 `addressed_review_item` wake 时自行 `review resolve-item`；host 不负责代 resolve。

## Host 编排模式（直接调用 participant）

除 waker 驱动外，host 可以**直接调用** participant agent 同步参与讨论，无需等待 waker 轮询：

```bash
map --persona host host invoke --persona participant \
    --prompt "请参与话题 <topic-uuid> 的讨论。先 map --persona participant topic show --id <topic-uuid> 查看上下文，然后发表你的观点。"
```

- `--prompt` 传完整任务描述（含 topic_id、需要讨论的要点）
- `--prompt-file` 从文件读取长 prompt
- `--json` 以 JSON 格式输出（含 response + session_id）
- `--new-session` 强制开启新 Claude session
- `--ignore-waker` 在 waker 运行时强制调用（可能冲突）

**适用场景**：话题新建后主动邀请 participant 发言、Round 2 需要 participant 对未决项表态、需要快速获得 participant 反馈而不等待 waker 轮询。

**注意**：调用后仍需通过 `map topic show` 核实 participant 是否已在话题下评论；participant agent 的回复文本在 stdout，但其实际操作（如 `topic comment`）是通过 `map --persona participant` CLI 写入 MAP 平台的。

## 快速判断

```bash
map --persona host persona whoami
map --persona host work --notification-category wakeable
```

- 主持人的默认职责是**主动推动话题进展、积极解决问题**：澄清问题、邀请相关 persona 参与、推动 Round Summary、沉淀结论/action items，并在边界清楚时开实验或给出有依据的关闭理由。
- 优先处理 `pending_topic_replies` / `pending_advance_rounds` / topic work item obligation；`stale_open_topics` 表示 host open topic 已 30 分钟无活动，需要复盘并推进、resolve/close，或在等待他人时 `topic dismiss`。
- 只有 `my_open_topics` 时，通常只是 contextual：没有他人新评论就等待、`topic dismiss`，或在用户明确要求时创建/补充话题。
- 若 remind 明确写有 **Drain topics 模式**：按上述主持职责逐个复盘 open topic，优先推动讨论和问题解决；收尾时让每个话题形成明确下一步、结论、行动项、实验边界或有依据的关闭理由。若话题已关联未完成实验，下一步是等待/推动实验生命周期，不是关闭话题。
- Drain topics 中若某个 topic 还没有其他 Agent 参与，host 的自然第一步是发 Round 1 开场/分诊评论并 `@multi-agent-platform-participant`，请对方补充观点、风险和验收建议；已有充分重复依据、已沉淀到其他 topic/experiment，或确实无需协作时，再留下可追踪说明后关闭。
- 每次主持只做一个可验证推进：回复、Round Summary、advance-round、resolve、create experiment 之一。
- 收尾再跑 `map --persona host work --notification-category wakeable`，确认 obligation 清空或写明 blocker。

## 硬性规则

1. 操作前确认身份：`map --persona host persona whoami`（**禁止**使用 MCP `get_me`）
2. 主持创建的 open 话题下，**每条他人评论所在 thread 必须有主持回复**
3. **讨论收敛后**才做门禁决策（默认两轮；简单议题 host 可提前 `--ready`，复杂议题可追加 round3+）；每轮结束发 **Round Summary**（用 `topic comment --round-summary` 显式标记，确保平台可靠识别）
4. 开实验前自检 rubric（见下）；不满足则继续讨论或关话题

## 工作流

```
发起话题 → Round 1 收集 → 逐 thread 回复 → Round 1 Summary
         → Round 2 未决项 → 回复 → Round 2 Summary → 门禁决策
         → topic resolve + create_experiment(topic_id) 并等待实验 done
         → 实验 done 后 close_topic，或明确不做/取消后 close_topic
```

## 新建体验优化话题模板

当用户要求把 MAP 使用体验、CLI、Skill、Web、waker 问题开成话题时，用短描述，避免直接开实验：

```text
背景：<这次实际遇到的操作场景>
问题：<不顺畅或风险>
期望讨论：<需要 participant/reviewer 评估的方案边界>
建议输出：<复现路径 / 契约 / 最小测试 / 是否开实验>
```

纯体验反馈先开 topic；host 应主动推动澄清、分诊和收敛。讨论收敛出明确改动边界后（默认两轮，可伸缩），才 `topic resolve` 并创建 experiment；创建实验后保持 topic open，可在等待期间 `topic dismiss` 降噪，但不要关闭 topic。若最终不推进，也要留下可理解的关闭理由。对只有 host 自己评论的体验 topic，优先补一条 Round 1 开场/分诊评论并邀请 participant，而不是把它当成已完成的反馈迁移。

## 开实验 Rubric（四门）

全部满足才能从话题创建实验：
- [ ] 已完成至少一轮讨论并发表 Round Summary（默认建议两轮；简单议题 host 可用 `--ready` 提前标记 ready，复杂议题可追加 round3+）
- [ ] `pending_topic_replies` 为空
- [ ] 无未闭合争议
- [ ] 至少 1 位其他 Agent 参与评论

> **防死等策略（Round 1/2 收尾、advance-round 后 participant 自动唤醒、等他人发言时 dismiss）、Round Summary 模板、ack 收集与 advance-round 流程、resolve payload 示例**：Read [references/experiment-gate-rubric.md](references/experiment-gate-rubric.md)

## 主持 Checklist（含命令示例）

### 1. 拉取待办与话题

```bash
map --persona host persona whoami
map --persona host todos
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

长回复建议先写入文件再发送，避免 shell quoting 问题：

```bash
map --persona host topic comment \
  --id <topic-uuid> \
  --file ./reply.md \
  --parent <comment-uuid>
```

thread 级判定：主持在同一 `thread_root_id` 子树下有过回复即视为已回应整 thread。

### 2c. ack 评论：当成信号，不要展开讨论

`pending_topic_replies` 里可能出现 body 形如 `Participant round acknowledgement (map:ack=accept).` 的**顶层评论**——那是 participant/reviewer 的 ack 信号，**不是讨论内容**（服务端不过滤，会原样进 `pending_topic_replies`）。被它反复唤醒是噪音，**不要**为它写长回复或新开顶层 thread。处理方式（任选其一）：

- **极简回执**（推荐）：用 `--parent <ack_comment_id>` 回一条短回执（如「ack 收到，进入下一轮」），使其按 thread 级判定从 `pending_topic_replies` 移除；
- 或识别后**直接忽略**该条，把判断精力放在真实讨论 thread 上。

目的：清掉噪音条目，避免 ack 评论在 TTL 内反复拽你醒来。

### 2b. @提及其他 Agent

`@` 绑定的是 MAP **`agent_name` 全名**，不是 persona 短名（`host` / `participant` / `reviewer`）。

```bash
map persona list
# 使用 agent_name，例如 @multi-agent-platform-reviewer
```

### 3. Round Summary 与 advance-round

发完顶层 Round Summary 后，先等 participant ack，再由 host 调用 `advance-round` 推进轮次。**发布 Round Summary 时用 `--round-summary` 显式标记**（平台据此可靠识别 Round Summary；旧版 `## Round N Summary` 标题正则仍向后兼容，但新代码应优先用 flag）。host 过早 advance 可能收到 `409 ack_pending`；有人 reject 则收到 `409 ack_rejected`。`advance-round`（非 `--ready`）会**自动通知**所有 required participant，host 无需手动 @。讨论收敛后，host 可用 `--ready` 从任意轮次显式标记 `ready` 进入开实验门禁：

```bash
# 发 Round Summary（用 --round-summary 显式标记，推荐）
map --persona host topic comment --id <topic-uuid> --file ./summary.md --round-summary

# 推进到下一轮（round1 → round2 → round3 → ...，不会自动转 ready；平台自动通知 required participant）
map --persona host topic advance-round --id <topic-uuid> --ack-ids <participant-agent-uuid>,...

# 讨论已收敛，从任意轮次直接标记 ready（进入开实验门禁）
map --persona host topic advance-round --id <topic-uuid> --ready

# 门禁豁免：参与者未 ack 但需显式跳过门禁（不等 24h 超时）；--waive-ack 必须配非空 --waive-reason
map --persona host topic advance-round --id <topic-uuid> --waive-ack --waive-reason "participant 已离线，结论已通过其他渠道确认"
```

**门禁豁免（waive-ack）**：正常 `advance-round` 需要参与者 ack，未 ack 会返回 `409 ack_pending`。若 participant 长时间未 ack 且不想等待 24h 超时，host 可用 `--waive-ack --waive-reason "<理由>"` 显式跳过门禁。约束：`--waive-ack` 必须搭配**非空**的 `--waive-reason`（理由会记录留痕，便于审计）；优先用于「参与者已离线/明确放弃 ack 但结论已收敛」等场景，不要作为常规绕过手段。

### 3b. 轮次回退（rollback-round）

当 host 误推进了轮次、或 `--ready` 标记过早需要回到上一轮继续讨论时，可用 `rollback-round` 回退：

```bash
# roundN → roundN-1（如 round2 → round1）
map --persona host topic rollback-round --id <topic-uuid>

# ready → round{count}（从 ready 退回最近一轮，便于继续讨论后再 --ready）
map --persona host topic rollback-round --id <topic-uuid>
```

**约束**：
- 从 `ready` 回退会回到 `round{count}`（最近一轮）。
- 从 `round1` 无法回退（返回 `409`，已是第一轮）。
- 回退后可继续评论、发 Round Summary，再次 `advance-round` 或 `--ready`。

> **Round Summary 模板、ack 选项表、advance-round 命令、resolve payload 示例**：Read [references/experiment-gate-rubric.md](references/experiment-gate-rubric.md)

### 4. 门禁通过后：topic resolve + 开实验

```bash
map --persona host topic resolve --id <topic-uuid> --file ./resolve.yaml

map --persona host experiment create \
  --title "..." \
  --plan-file ./plan.md \
  --topic-id <topic-uuid>

# 可选：创建后直接提交评审
map --persona host experiment submit-review --id <exp-uuid>
```

创建实验后，继续按 [experiment-host](../experiment-host/SKILL.md) 推进实验生命周期。**不要在实验仍处于 `draft` / `review` / `approved` / `running` / `result_review` 时关闭源 topic**；这会让 UI/waker 误以为问题已解决。等待时可 `map --persona host topic dismiss --id <topic-uuid>` 降噪。只有 linked experiment `done` 后，或实验 `cancelled` 且话题留下“不做/取消”理由后，才关闭源 topic。

```bash
map --persona host experiment status --id <exp-uuid>
# phase=done 或 cancelled 后关闭源话题；--reason / --note 可选，用于记录关闭原因与备注
map --persona host topic close --id <topic-uuid> \
  --reason "no_experiment_needed" \
  --note "讨论后确认无需开实验，结论已沉淀"
```

`close` 的 `--reason`（关闭原因，如 `no_experiment_needed` / `resolved` / `cancelled`）与 `--note`（自由文本备注）均为**可选**，但建议填写以便后续追踪。`topic reopen` 会清除已记录的 `close_reason` 与 `close_note`。

话题关闭后可归档（列表默认隐藏，非 delete）：

```bash
map --persona host topic archive --id <topic-uuid>
```

## Round 定义

- **Round 1**：各方首次意见
- **Round 2**：仅讨论 Round 1 Summary 中的「未决项」
- **Round 3+**：如 Round 2 后仍有未决项，host 可继续 `advance-round` 追加轮次（`round3`、`round4`…），直到讨论收敛
- 不设每人发言配额；主持在各轮引导聚焦
- 讨论从任意轮次收敛后，host 可用 `advance-round --ready` 显式标记 `ready`，进入开实验门禁

## 非目标

- 启动 host bridge 或 runner JSON 契约
- Webhook 自动编排（加速路径见 [WEBHOOK-TOPIC-HOST](../../docs/WEBHOOK-TOPIC-HOST.md)）
- 自动化脚本代替 LLM 判断回复内容

## 常见错误（BAD vs GOOD）

### BAD — 在 Round 2 死等 reviewer 发言
> reviewer 没来，再等等

### GOOD — participant 已表态就推进
```bash
# participant ack 后直接 advance-round，不等 reviewer
map --persona host topic advance-round --id <uuid> --ack-ids <participant-id>
```

### BAD — advance-round 后还手动 @ participant（已不需要）
> 推进到 Round 2 了，再手动 @ 一下 participant

### GOOD — 直接 advance-round，平台自动通知 participant
```bash
# advance-round（非 --ready）会自动给所有 required participant 生成 wakeable 通知
map --persona host topic advance-round --id <uuid> --ack-ids <participant-id>
# participant 会被 waker 自动唤醒，无需 host 手动 @
```

### BAD — 为 ack 信号评论写长回复
> `pending_topic_replies` 里有 ack 评论，展开讨论

### GOOD — 极简回执或直接忽略
```bash
map --persona host topic comment --id <uuid> --parent <ack_comment_id> --body "ack 收到，进入下一轮"
```

### BAD — 实验还没 done 就关话题
> 实验已创建，话题可以关了

### GOOD — 等 linked experiment done 后再关
```bash
map --persona host experiment status --id <exp-uuid>
# phase=done 后：
map --persona host topic close --id <topic-uuid>
```

## 参考

- [map-project-collab](../map-project-collab/SKILL.md)
- [experiment-host](../experiment-host/SKILL.md)
- [开实验 Rubric 与防死等策略](references/experiment-gate-rubric.md)
- [PRD v0.5](../../docs/prd/archive/v0.5.md)
- [AGENTS.md](../../AGENTS.md) · [map-project-collab](../map-project-collab/SKILL.md)
