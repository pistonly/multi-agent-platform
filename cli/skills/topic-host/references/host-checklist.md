# Host 主持命令手册（Checklist）

> 从 [topic-host SKILL.md](../SKILL.md) 下沉的命令大全。执行具体主持动作时按节查阅；开实验四门判断与防死等策略见 [experiment-gate-rubric.md](experiment-gate-rubric.md)。文件引用模式（`--file-path`）详见 [map-project-collab file-reference](../../map-project-collab/references/file-reference.md)。
>
> **v0.13 M58 起话题写操作 FS 单轨化**：写命令一律走 `map fs ...`；DB 写命令（`topic create / comment / advance-round / resolve / rollback-round / reopen / close / archive`）已退役，调用返回引导性错误与 fs 等价指引。

## 1. 拉待办与话题状态

```bash
map --persona host topic list --status open
map --persona host topic show --id <topic-uuid>     # 存量 DB 话题只读
map --persona host fs list                          # FS 话题清单
map --persona host fs show --topic <slug>           # FS 话题实时解析
map --persona host topic progress                   # topic work items 投影（与 todos 话题分区同源）
map --persona host todos
map --persona host work                             # 统一快照：whoami + topic-progress + todos + wakeable 通知
```

**读全量内容**：FS 话题用 `fs show`（含每轮发言文件全文）；存量 DB 话题 `topic show` 返回树形 `comment_tree`，按 thread 逐条读，**不要**只读最后一条，`file_path` 评论直接读本地 MD 文件全文。

## 2. 发言与回复（FS 模式）

```bash
# 短发言：内联 body
map --persona host fs comment --topic <slug> --body "回复内容"

# 长发言 / Round Summary：本地 MD 文件（推荐）
# 路径约定：map/topics/<slug>/round<N>-host.md（同轮覆盖需 --force，遵守 immutable 约定）
map --persona host fs comment --topic <slug> --file map/topics/<slug>/round1-host.md

# 发布 Round Summary（显式标记，平台据此可靠识别）
map --persona host fs comment --topic <slug> --file map/topics/<slug>/round1-summary-host.md --round-summary
```

@ 必须用 `map persona list` 的 **agent_name 全名**（如 `@multi-agent-platform-participant`）；FS 评论 @ 提及只起视觉提示作用（唤醒依赖 `fs advance-round` 事件与待办投影，见 2c）。

## 2b. 处理 participant 的表态发言

FS 模型里 participant 的表态即「写本轮发言文件」。host **无需**催收或回复「收到」——发言文件出现在 `map/topics/<slug>/` 即算表态，读到了直接推进下一步（`fs advance-round` 或开实验）。

## 2c. Round Summary 与轮次推进

```bash
# 参与者本轮发言补齐后推进轮次
map --persona host fs advance-round --topic <slug>
# 未满员 409 会列出 missing agents；确需推进：
map --persona host fs advance-round --topic <slug> --waive-ack --waive-reason "参与者离线，结论已收敛"

# 讨论已收敛：推进并标记 ready（进入开实验门禁）
map --persona host fs advance-round --topic <slug> --mark-ready
```

- Summary 正文末尾 **@ 所有需表态的 agent 全名**（视觉锚点；实际唤醒走事件与投影）
- `fs advance-round` 后平台对 required participant 产生待办投影（`pending_topic_reply`），无需再手动 @
- 收到 `409`：还有人未交本轮发言，等待或 `--waive-ack --waive-reason`；有人明确反对时在该轮文件 @ 拒绝者继续讨论，**不要**强制推进

**回退一轮（FS 等价约定，v0.13 M58 定案）**：删除本轮次参与者的 round 文件（`map/topics/<slug>/round<N>-<persona>.md`），随后 `map fs scan`（或 `fs show`）核对 `index.md` 的 `round` 计数与 participants 一致性，必要时手工修正 index frontmatter 后再继续。

## 3. 沉淀结论与开实验（仅 host）

```bash
# 结论沉淀合并进关闭动作：close_note 承载 decision / rationale / action_items
# payload 结构见 experiment-gate-rubric.md
map --persona host fs close --topic <slug> \
  --reason experiment_ready --note "decision: ...; rationale: ...; action_items: [{item, owner}]"

# 从话题开实验（四门见 experiment-gate-rubric.md）
map --persona host experiment create \
  --title "..." \
  --plan-file ./plan.md \
  --topic-id <topic-uuid>

# 瘦身模式：只存计划文件路径
map --persona host experiment create \
  --title "..." \
  --plan-file-path map/experiments/<slug>/plan.md \
  --topic-id <topic-uuid>

# 实验生命周期移交给 experiment-host Skill
```

关闭并开实验后动作参见 [experiment-host](../../experiment-host/SKILL.md)。

## 4. 关闭话题与存量处置

```bash
# FS 话题关闭（--reason/--note 可选；结论放 note）
map --persona host fs close --topic <slug> --reason no_experiment_needed --note "讨论后决定不开实验"

# 重开（FS 等价约定）：把 index.md frontmatter 的 status 改回讨论中，
# 并在 index 或下一轮 round 文件开头说明重开原因

# 归档（FS 等价约定）：把 map/topics/<slug>/ 目录移动到 map/archive/topics/<slug>/，
# 列表默认隐藏、show 仍可见（archive ≠ delete）；反向移动即撤销归档

# 等他人发言时降噪（保留命令，与 Web UI ✕ 相同，双视图同时消失）
map --persona host topic dismiss --id <topic-uuid>

# 存量 DB 话题仍有讨论价值：读全量后迁移为 FS 话题（迁移后源话题自动归档）
map --persona host topic migrate --id <topic-uuid>
```

实验处于 `draft/review/approved/running/result_review` 时**不要**关闭源话题，等待期间用 `dismiss` 降噪。

## 附录：新建体验优化话题模板

用户反馈 MAP 平台体验问题时，用体验优化话题模板（而非普通话题）创建：

```bash
map --persona host fs topic-create --slug <name> --title "体验优化：<一句话主题>"
map --persona host fs comment --topic <slug> --file ./init.md   # 描述用户反馈的原始问题，含场景与期望
```

正文评论附复现步骤与影响面；这类话题收敛后的产物通常是对 MAP 平台的改进项（可 `map feedback submit` 或开实验），而不是对本仓库业务代码的直接修改。
