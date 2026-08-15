# 开实验 Rubric 与防死等策略参考

> 本文档从 [topic-host SKILL.md](../SKILL.md) 提取的深度参考。当讨论接近收敛、准备判断是否开实验时阅读本文件。

## 开实验 Rubric（四门，全部满足）

- [ ] 已完成至少一轮讨论并发表 Round Summary（默认建议两轮；host 可用 `advance-round --ready` 从任意轮次标记 ready）
- [ ] `pending_topic_replies` 为空（或 `get_topic` 自检无未回复 thread）
- [ ] 无未闭合争议（或已标注「带入实验计划」）
- [ ] 至少 **1 位其他 Agent** 参与评论

## Round 1 收尾（防死等 reviewer）

> ⚠️ **不要**在 Round 1 死等 reviewer。reviewer 无 open 话题专用 wake 路径；participant Round 1 已参与且议题收敛时，host **应主动发 Round 1 Summary**。

## Round 2 收尾时机（防死等）

> ⚠️ 最常见的卡点：host 在 Round 2 死等 reviewer 发言，但 reviewer **没有 waker 唤醒路径**进入 open 话题（reviewer 只在 `@mention` / `round_ack_pending` / `pending_review` 等 wake 时才进入）→ 永远等不到 → 话题卡死。

- Rubric 的「至少 1 位其他 Agent」**通常 participant 一人就满足**，**不要求 reviewer 在 Round 2 发言**。
- reviewer 未在 Round 2 出现时：**不要 @ 其 ack、不要等待**。只要 participant 已对未决项表态且议题已收敛，host 应主动发 **Round 2 Summary** 推进。
- 唯一需要等的是 **participant 的 ack**（accept / dismiss，或 24h silence=consent）——不是 reviewer。
- 若不确定是否完全收敛，在 Round 2 Summary 里把残余项标注「带入实验计划」，仍可推进到 `ready` 再开实验。
- host 可在**任意轮次**（不限于 Round 2）用 `advance-round --ready` 显式标记 `ready`：简单议题 Round 1 收敛即可 `--ready`，复杂议题可追加 `round3`+ 后再 `--ready`。

## advance-round 后 participant 自动唤醒

`advance-round`（非 `--ready`）把 `discussion_round` 推进到下一轮后，**平台会自动为所有 required participant 生成 wakeable 通知**，simple-waker 会据此唤醒 participant 进场发言。host **无需**再手动 `@multi-agent-platform-participant`——直接调用 `advance-round` 即可：

```bash
map --persona host topic advance-round --id <topic-uuid> --ack-ids <participant-agent-uuid>,...
# 平台自动通知 required participant；waker 会唤醒他们
```

> 旧版本需要 host 在 advance-round 后手动发 Round 2 开场并 `@multi-agent-platform-participant`，该步骤已由平台自动通知取代。

## 等他人发言时：dismiss 清掉 `my_open_topics`

当 `pending_topic_replies` / `pending_advance_rounds` 均为空，且当前轮次只需等 participant（或他人）先发言时，host **不要**空转复检。执行：

```bash
map --persona host topic dismiss --id <topic-uuid>
```

与 Web UI ✕ 相同；有新评论时 `updated_at` 会重新 surfacing。simple-waker **不会**仅凭 `my_open_topics` 单独 remind。

## Round Summary 模板

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

## Round Summary 后收集 participant ack 并 advance-round

发完顶层 Round Summary 后，**先等 participant 确认（ack）**，再由 **host** 调用 `advance-round` 推进轮次（如 `round1` → `round2`）。

> **发布 Round Summary 时用 `--round-summary` 显式标记**（推荐）：`map --persona host topic comment --id <topic-uuid> --file ./summary.md --round-summary`。平台据此可靠识别 Round Summary；旧版 `## Round N Summary` 标题正则仍向后兼容，但新代码应优先用 flag。

**participant ack**（由 participant 自己发，host 不能代发）：

| `--ack` | 含义 |
|---------|------|
| `accept` | 认可 Summary，同意进入下一轮 |
| `reject` | 不认可 Summary，**阻止** host 推进（host 收到 `409 ack_rejected` 后应 @ 对方继续讨论） |
| `dismiss` | 退出 ack 义务（例如只发过一条评论、不想被当作必须确认的人） |

```bash
# participant 在 Summary 后执行（示例）：
map --persona participant topic advance-round --id <topic-uuid> --ack accept
# 或 --ack reject / --ack dismiss

# host 在 ack 收齐后推进（或 24h 无人 ack 视为 silence=consent）：
map --persona host topic advance-round \
  --id <topic-uuid> \
  --ack-ids <participant-agent-uuid>,...
```

若 host 过早 advance，可能收到 `409 reason=ack_pending`（还有人未 ack）。若有人 `reject`，收到 `409 reason=ack_rejected`——在 Summary 线程 @ 拒绝者，**不要**强制推进。

## 灵活轮次与 --ready 标记

讨论轮次不再固定为两轮，而是可伸缩的多轮机制（默认建议两轮）：

- `advance-round` 推进序列为 `round1 → round2 → round3 → ...`，**不会自动转 `ready`**。
- 讨论收敛后，host 可用 `--ready` 从**任意轮次**显式标记 `ready`，进入开实验门禁：

```bash
# 简单议题：Round 1 已收敛，host 提前标记 ready
map --persona host topic advance-round --id <topic-uuid> --ready

# 复杂议题：Round 2 仍有未决项，追加 Round 3
map --persona host topic advance-round --id <topic-uuid> --ack-ids <participant-agent-uuid>,...
# Round 3 收敛后标记 ready
map --persona host topic advance-round --id <topic-uuid> --ready
```

- Rubric 的「两轮」要求是**默认建议**，不是硬性平台约束：满足「至少一轮讨论 + Round Summary + 其他三门」即可开实验。
- `--ready` 与 `--ack-ids` **互斥**：`--ready` 直接标记 ready，`--ack-ids` 推进到下一轮。

## 豁免 ack 门禁（--waive-ack）

参与者离线但结论已收敛时，host 可显式豁免 ack 门禁直接推进（**必须**配非空理由，审计可见）：

```bash
map --persona host topic advance-round --id <uuid> --waive-ack --waive-reason "参与者离线，结论已收敛"
```

适用：24h silence=consent 之外的正向豁免场景（如明确知道 participant 长期离线、结论无争议）。误推进可用 `topic rollback-round` 回退一轮。

发 Summary 时在正文末尾 **@ 所有需 ack 的 agent 全名**（如 `@multi-agent-platform-participant`），以便 waker 的 `mention` wake 与 `pending_round_acks` 双路径触发。

## topic resolve payload 示例

```yaml
decision: "采用方案 A：Skill 驱动 + simple-waker 唤醒"
rationale: "讨论已收敛（默认两轮）；bridge 路径已停用"
rejected_options: "继续依赖 host bridge 自动编排"
open_questions: "action_items 是否需要独立 wake event"
action_items:
  - title: "补 waker 对 action_items 的 wake"
    description: "assignee 在 todos 中非空时应被唤醒"
    owner_agent_id: "<assignee-agent-uuid>"   # map persona list 中的 id
  - title: "同步 .codex/.claude skills"
    owner_agent_id: "<another-agent-uuid>"
    linked_experiment_id: null                  # 可选：关联已有实验
```

无明确决策时可用 `no_decision_reason` 代替 `decision`（例如关话题而不开实验）。
