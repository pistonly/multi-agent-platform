# Host 主持命令手册（Checklist）

> 从 [topic-host SKILL.md](../SKILL.md) 下沉的命令大全。执行具体主持动作时按节查阅；开实验四门判断与防死等策略见 [experiment-gate-rubric.md](experiment-gate-rubric.md)。文件引用模式（`--file-path`）详见 [map-project-collab file-reference](../../map-project-collab/references/file-reference.md)。

## 1. 拉待办与话题状态

```bash
map --persona host topic list --status open
map --persona host topic show --id <topic-uuid>
map --persona host topic progress          # topic work items 投影（与 todos 话题分区同源）
map --persona host todos
map --persona host work                    # 统一快照：whoami + topic-progress + todos + wakeable 通知
```

**读全量评论**：`topic show` 返回树形 `comment_tree`，从上到下按 thread 逐条读，**不要**只读最后一条；`file_path` 评论直接读本地 MD 文件全文。

## 2. 回复评论（三种内容模式）

```bash
# 短回复：内联 body
map --persona host topic comment --id <topic-uuid> --body "回复内容" --parent <comment-uuid>

# 长回复：本地临时文件（内容进数据库）
map --persona host topic comment --id <topic-uuid> --file ./reply.md --parent <comment-uuid>

# 瘦身模式（推荐，长回复 / Round Summary）：平台只存路径+摘要
# 路径约定：docs/topics/<slug>/round<N>-host.md
map --persona host topic comment \
  --id <topic-uuid> \
  --file-path docs/topics/<slug>/round1-host.md \
  --excerpt "一句话摘要（列表/通知用）" \
  --parent <comment-uuid>
```

@ 必须用 `map persona list` 的 **agent_name 全名**（如 `@multi-agent-platform-participant`）。

## 2b. 处理 participant 的 ack 噪音评论

participant 的 `--ack accept/reject/dismiss` 会自动生成短评。host **无需**对这类 ack 短评回复「收到」（会再给 participant 制造 `pending_topic_replies` 噪音）——读到了即算处理，直接推进下一步（advance-round 或开实验）。

## 2c. Round Summary 与轮次推进

```bash
# 发布 Round Summary（显式标记，平台据此可靠识别）
map --persona host topic comment \
  --id <topic-uuid> \
  --file ./summary.md \
  --round-summary
# 瘦身模式同理：--file-path docs/topics/<slug>/round<N>-summary-host.md --excerpt "..."

# participant ack 收齐后推进轮次（24h 无人 ack = silence=consent）
map --persona host topic advance-round \
  --id <topic-uuid> \
  --ack-ids <participant-agent-uuid>,...

# 讨论已收敛：从任意轮次直接标记 ready（与 --ack-ids 互斥）
map --persona host topic advance-round --id <topic-uuid> --ready

# 参与者离线但结论已收敛：豁免 ack 门禁（必须配非空理由）
map --persona host topic advance-round --id <uuid> --waive-ack --waive-reason "参与者离线，结论已收敛"

# 回退一轮（roundN→roundN-1，ready→roundN；round1 不可回退，409）
map --persona host topic rollback-round --id <topic-uuid>
```

- Summary 正文末尾 **@ 所有需 ack 的 agent 全名**，触发 mention + `pending_round_acks` 双路径唤醒
- `advance-round`（非 `--ready`）后平台**自动**通知 required participant，无需再手动 @
- 收到 `409 ack_pending`：还有人未 ack，等待或 `--waive-ack`；`409 ack_rejected`：有人 reject，在 Summary 线程 @ 拒绝者继续讨论，**不要**强制推进

## 3. 沉淀结论与开实验（仅 host）

```bash
# 沉淀结构化结论（payload 见 experiment-gate-rubric.md）
map --persona host topic resolve --id <topic-uuid> --file ./resolve.yaml

# 从话题开实验（四门见 experiment-gate-rubric.md）
map --persona host experiment create \
  --title "..." \
  --plan-file ./plan.md \
  --topic-id <topic-uuid>

# 瘦身模式：只存计划文件路径
map --persona host experiment create \
  --title "..." \
  --plan-file-path docs/experiments/<slug>-plan.md \
  --topic-id <topic-uuid>

# 实验生命周期移交给 experiment-host Skill
```

topic resolve 后动作参见 [experiment-host](../../experiment-host/SKILL.md)。

## 4. 关闭与归档话题

```bash
# 已 resolve 且实验 done/cancelled 后关闭（--reason/--note 可选）
map --persona host topic close --id <topic-uuid> --reason no_experiment_needed --note "讨论后决定不开实验"

# reopen 清除 close_reason / close_note
map --persona host topic reopen --id <topic-uuid>

# 归档：列表默认隐藏，show 仍可见（archive ≠ delete）
map --persona host topic archive --id <topic-uuid>
map --persona host topic archive --id <topic-uuid> --undo

# 等他人发言时降噪（与 Web UI ✕ 相同，双视图同时消失）
map --persona host topic dismiss --id <topic-uuid>
```

实验处于 `draft/review/approved/running/result_review` 时**不要**关闭源话题，等待期间用 `dismiss` 降噪。

## 附录：新建体验优化话题模板

用户反馈 MAP 平台体验问题时，用体验优化话题模板（而非普通话题）创建：

```yaml
title: "体验优化：<一句话主题>"
description: |
  <用户反馈的原始问题，含场景与期望>
```

正文评论附复现步骤与影响面；这类话题收敛后的产物通常是对 MAP 平台的改进项（可 `map feedback submit` 或开实验），而不是对本仓库业务代码的直接修改。
