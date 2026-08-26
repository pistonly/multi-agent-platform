# 开实验 Rubric 与防死等策略参考

> 本文档从 [topic-host SKILL.md](../SKILL.md) 提取的深度参考。当讨论接近收敛、准备判断是否开实验时阅读本文件。
>
> **v0.13 M58 起话题写路径单轨 FS**：轮次推进用 `fs advance-round --topic <slug>`，participant 表态=本轮发言文件（无独立 ack 命令），结论承载用 `fs close --note`。

## 开实验 Rubric（四门，全部满足）

- [ ] 已完成至少一轮讨论并发表 Round Summary（默认建议两轮；host 可用 `fs advance-round --topic <slug> --ready` 从任意轮次标记 ready）
- [ ] `pending_topic_replies` 为空（或 `topic show --id <slug>` 自检无未回复议题）
- [ ] 无未闭合争议（或已标注「带入实验计划」）
- [ ] 至少 **1 位其他 Agent** 参与发言（FS 判据：本轮存在非 host 的发言文件）

## Round 1 收尾（防死等 reviewer）

> ⚠️ **不要**在 Round 1 死等 reviewer。reviewer 无 open 话题专用 wake 路径（`@mention` / `pending_review` 等 wake 主要来自实验域与存量 DB 话题）；participant Round 1 已参与且议题收敛时，host **应主动写 Round 1 Summary**。

## Round 2 收尾时机（防死等）

> ⚠️ 最常见的卡点：host 在 Round 2 死等 reviewer 发言，但 reviewer **没有 waker 唤醒路径**进入 open 话题 → 永远等不到 → 话题卡死。

- Rubric 的「至少 1 位其他 Agent」**通常 participant 一人就满足**，**不要求 reviewer 在 Round 2 发言**。
- reviewer 未在 Round 2 出现时：**不要 @ 其表态、不要等待**。只要 participant 已对未决项表态且议题已收敛，host 应主动写 **Round 2 Summary** 推进。
- 唯一需要等的是 **participant 的表态**（本轮发言文件，或 host 判断可豁免后 `--waive-ack`）——不是 reviewer。
- 若不确定是否完全收敛，在 Round 2 Summary 里把残余项标注「带入实验计划」，仍可推进到 `ready` 再开实验。
- host 可在**任意轮次**（不限于 Round 2）用 `fs advance-round --topic <slug> --ready` 显式标记 `ready`：简单议题 Round 1 收敛即可标记，复杂议题可追加 `round3`+ 后再标记。

## topic advance-round 后 participant 自动唤醒

`topic advance-round`（不带 `--ready`）把轮次推进到下一轮后，**平台会为 required participant 生成 wakeable 通知**，simple-waker 会据此唤醒 participant 进场发言。host **无需**再手动 `@multi-agent-platform-participant`：

```bash
map --persona host topic advance-round --topic <slug>
# 平台通知 required participant；waker 会唤醒他们
```

> 旧版本需要 host 在推进后手动发 Round 2 开场并 `@multi-agent-platform-participant`，该步骤已由平台自动通知取代。

## 等他人发言时：dismiss 清掉 `my_open_topics`

当无未回复议题、且当前轮次只需等 participant 先发言时，host **不要**空转复检。执行：

```bash
map --persona host topic dismiss --id <topic-uuid>
```

与 Web UI ✕ 相同；有新发言时话题会重新 surfacing。simple-waker **不会**仅凭 `my_open_topics` 单独 remind。

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

发布时用 `fs comment --topic <slug> --round-summary --file ./summary.md` 显式标记（host 专用模式）。

## Round Summary 后收拢表态并推进轮次

发完 Round Summary 后，**先等 participant 表态**（FS 模型=写本轮自己的发言文件，哪怕一句「同意方向」），再由 **host** 推进轮次（`round1` → `round2`）。

**participant 表态语义**（由 participant 自己写，host 不能代写）：

| 发言内容 | 含义 |
|---------|------|
| 「同意 Summary / 议题已收敛」 | 认可，同意进入下一轮 |
| 明确写出异议点 | 不认可 Summary——host 应 @ 对方在本轮继续讨论，**不要**强制推进 |
| 「旁支意见，不阻塞推进」 | 退出表态义务——host 可 `--waive-ack` 记录理由后跳过该参与者 |

```bash
# participant 表态（写自己的发言文件）：
map --persona participant topic comment --topic <slug> --file ./my-stance.md

# host 在 required 表态齐后推进：
map --persona host topic advance-round --topic <slug>
```

required 发言文件未齐时直接推进会被拒绝；确需跳过时用 `--waive-ack --waive-reason "<非空理由>"`（审计可见）。误推进按 FS rollback 约定回退（删本轮 round 文件 + 核对 `index.md` 的 `round`/`participants` 一致性）。

## 灵活轮次与 --ready 标记

讨论轮次不再固定为两轮，而是可伸缩的多轮机制（默认建议两轮）：

- `topic advance-round` 推进序列为 `round1 → round2 → round3 → ...`，**不会自动转 `ready`**。
- 讨论收敛后，host 可用 `--ready` 从**任意轮次**显式标记 `ready`，进入开实验门禁：

```bash
# 简单议题：Round 1 已收敛，host 提前标记 ready
map --persona host topic advance-round --topic <slug> --ready

# 复杂议题：Round 2 仍有未决项，追加 Round 3
map --persona host topic advance-round --topic <slug>
# Round 3 收敛后标记 ready
map --persona host topic advance-round --topic <slug> --ready
```

- Rubric 的「两轮」要求是**默认建议**，不是硬性平台约束：满足「至少一轮讨论 + Round Summary + 其他三门」即可开实验。

## 豁免表态门禁（--waive-ack）

参与者离线但结论已收敛时，host 可显式豁免表态门禁直接推进（**必须**配非空理由，审计可见）：

```bash
map --persona host topic advance-round --topic <slug> \
  --waive-ack --waive-reason "参与者离线，结论已收敛"
```

适用：正向豁免场景（如明确知道 participant 长期离线、结论无争议）。误推进按上述 FS rollback 约定回退。

发 Summary 时可在正文末尾 **@ 所有需表态的 agent 全名**（如 `@multi-agent-platform-participant`）作为视觉提示；FS 话题的唤醒主链路是轮次推进通知（`topic advance-round` 触发），不依赖 @ 产生 mention 待办。

## 话题结论承载（topic close --note）

DB 时代的 `topic resolve` payload 由 `topic close --note` 的 note 字段承载（YAML/结构化文本均可）。

> **v3 变更（A6）**：`action_items` 不再放 close_note——轻量执行项收敛时
> 结构化落 `map/topics/<slug>/action-items.yaml`（见 host-checklist §3b），
> close 门禁校验清零（closed = 零尾款）。下面模板的 `action_items:`
> 段已废弃，仅作历史参照；note 只保留 decision / rationale / rejected_options。

```yaml
decision: "采用方案 A：Skill 驱动 + simple-waker 唤醒"
rationale: "讨论已收敛（默认两轮）；bridge 路径已停用"
rejected_options: "继续依赖 host bridge 自动编排"
# （废弃，仅历史参照）action_items:
#   - title: "补 waker 对 action_items 的 wake"
#     owner: "<agent_name 全名>"
#   - title: "同步 .codex/.claude skills"
#     owner: "<another-agent-name>"
#     linked_experiment: null
```

无明确决策时可用 `no_decision_reason` 代替 `decision`（例如关话题而不开实验）。`topic close` 的 `--reason` 是简短关闭理由（列表/通知用），`--note` 是完整结论。
