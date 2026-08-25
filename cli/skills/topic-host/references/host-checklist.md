# Host 主持命令手册（Checklist）

> 从 [topic-host SKILL.md](../SKILL.md) 下沉的命令大全。执行具体主持动作时按节查阅；开实验四门判断与防死等策略见 [experiment-gate-rubric.md](experiment-gate-rubric.md)。文件引用模式（`--file-path`）详见 [map-project-collab file-reference](../../map-project-collab/references/file-reference.md)。
>
> **写操作发现入口是 `map fs`**（与看板 / `map topic --help` 一致）：`topic-create` / `comment` / `advance-round` / `close` / `archive`。`map topic` 对应写子命令仍可用（隐藏兼容别名）。对 DB uuid 的写命令已退役，调用返回引导性错误。

## 1. 拉待办与话题状态

```bash
map --persona host topic list --status open
map --persona host topic show --id <slug>           # 新话题用 slug；存量 DB 用 uuid
map --persona host topic progress                   # topic work items 投影（与 todos 话题分区同源）
map --persona host todos
map --persona host work                             # 统一快照：whoami + topic-progress + todos + wakeable 通知
# 无网时：map fs list / map fs show --topic <slug>
```

**读全量内容**：`topic show --id <slug>`（含每轮发言文件）；存量 DB 话题返回树形 `comment_tree`，按 thread 逐条读，**不要**只读最后一条，`file_path` 评论直接读本地 MD 文件全文。

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

@ 必须用 `map persona list` 的 **agent_name 全名**（如 `@multi-agent-platform-participant`）；FS 评论 @ 提及只起视觉提示作用（唤醒依赖 `topic advance-round` 事件与待办投影，见 2c）。

## 2b. 处理 participant 的表态发言

FS 模型里 participant 的表态即「写本轮发言文件」。host **无需**催收或回复「收到」——发言文件出现在 `map/topics/<slug>/` 即算表态，读到了直接推进下一步（`topic advance-round` 或开实验）。

## 2c. Round Summary 与轮次推进

```bash
# 参与者本轮发言补齐后推进轮次
map --persona host fs advance-round --topic <slug>
# 未满员 409 会列出 missing agents；确需推进：
map --persona host fs advance-round --topic <slug> --waive-ack --waive-reason "参与者离线，结论已收敛"

# 讨论已收敛：推进并标记 ready（进入开实验门禁）
map --persona host fs advance-round --topic <slug> --ready
```

- Summary 正文末尾 **@ 所有需表态的 agent 全名**（视觉锚点；实际唤醒走事件与投影）
- `topic advance-round` 后平台对 required participant 产生待办投影（`pending_topic_reply`），无需再手动 @
- 收到 `409`：还有人未交本轮发言，等待或 `--waive-ack --waive-reason`；有人明确反对时在该轮文件 @ 拒绝者继续讨论，**不要**强制推进

**回退一轮（FS 等价约定，v0.13 M58 定案）**：删除本轮次参与者的 round 文件（`map/topics/<slug>/round<N>-<persona>.md`），随后 `map topic show --id <slug>` 核对 `index.md` 的 `round` 计数与 participants 一致性，必要时手工修正 index frontmatter 后再继续。

## 3. 沉淀结论与开实验（仅 host）

```bash
# 先开实验（409 门禁：不能在 closed 话题上 create，故顺序固定为 create → close）
map --persona host experiment create \
  --title "..." \
  --plan-file ./plan.md \
  --topic-id <topic-ref>          # uuid 或 FS slug（T2-P1 双路由）

# 瘦身模式：只存计划文件路径
map --persona host experiment create \
  --title "..." \
  --plan-file-path map/experiments/<slug>/plan.md \
  --topic-id <topic-ref>

# 后关话题：close_note 只承载 decision / rationale 与实验 id
# （执行项 NOT 写进 close_note —— 它们在收敛时就落进 action-items.yaml，见 §3b）
# payload 结构见 experiment-gate-rubric.md
#
#   没有挂实验的轻量执行项：
map --persona host topic action-item add --topic <slug> --owner <persona> --title "..."
#   owner 完成后带证据关单 / 显式放弃：
map --persona host topic action-item complete --topic <slug> --id <n> --evidence "<commit/pytest/路径>"
map --persona host topic action-item cancel --topic <slug> --id <n> --reason "..."
#
#   挂实验的项走实验（my_open_experiments/pending_reviews 义务已覆盖，防双催，D7）
map --persona host fs close --topic <slug> \
  --reason experiment_ready --note "decision: ...; rationale: ...; 实验 <exp-id>"
#   close 门禁（D2）:action-items.yaml 仍有 status: open 项 → 409 拦下
#   （closed = 零尾款）；全 done/cancelled 或无执行项即放行。

# 实验生命周期移交给 experiment-host Skill
```

## 3b. 执行项(轻量)落 action-items.yaml，不塞 close_note（plan v3 D1）

**结构化时点 = 话题收敛时（Round Summary / ready），不是 close 时。**

- 载体：`map/topics/<slug>/action-items.yaml`（列表文档，无 front-matter 围栏）。
- 每项字段：`id`（可省，手写按顺序 1-based 兜底）、`title`、`owner`
  （persona 短名 host/participant/reviewer）、`status`（open|done|cancelled）、
  `evidence`（done 必填：commit hash / pytest 摘要 / 文件路径）、
  `reason`（cancelled 必填）、`created_at`。
- 收敛时 host 落盘 open 项（`map topic action-item add`），owner 在
  `map work` 义务（kind=action_items）督促下执行，完成后
  `map topic action-item complete --evidence ...` 关单；
  明确不做则 `cancel --reason ...`。
- **close 门禁**：`map fs close` 时 server 校验 action-items.yaml 无
  `status: open` 项才放行；有则 409 并列出各项 title/owner 引导清零。
  closed = 零尾款是平台 invariant，**先清零再 close**。
- 旧 close_note 里写 action_items 文本的约定**已废弃**（A6）——存量已 close
  话题不回填不重解析，改了文档即拦截新写法。

关闭并开实验后动作参见 [experiment-host](../../experiment-host/SKILL.md)。

## 4. 关闭话题与存量处置

```bash
# FS 话题关闭（--reason/--note 可选；结论放 note）
map --persona host fs close --topic <slug> --reason no_experiment_needed --note "讨论后决定不开实验"

# 重开（FS 等价约定）：把 index.md frontmatter 的 status 改回讨论中，
# 并在 index 或下一轮 round 文件开头说明重开原因

# 归档：map fs archive --topic <slug>
# 列表默认隐藏、show 仍可见（archive ≠ delete）；--undo 撤销归档

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

正文评论附复现步骤与影响面；这类话题收敛后的产物通常是对 MAP 平台的改进项（v0.15 M62 起 `map feedback` 已退役：bug 开 GitHub issue、改进想法开 MAP 话题，或直接开实验），而不是对本仓库业务代码的直接修改。
