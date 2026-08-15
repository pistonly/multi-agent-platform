---
name: map-project-collab
description: >-
  Collaborate on MAP (Multi-Agent Platform) from a code repo using project-local
  .map/ personas. Use when the user asks to bootstrap MAP, choose
  host/participant/reviewer identity, list open topics, join topic discussions,
  check todos, action_items, pending_topic_replies, topic resolve, archive
  topics/experiments, submit platform feedback, run experiment lifecycle
  commands, or use map CLI with --persona. Waker mode: when resumed by the
  simple-waker daemon, read references/wake.md first for the minimal wake
  protocol and kind-to-cleanup dispatch table. For manual collaboration read
  this Skill first, then the persona Skill.
---

# MAP 项目协作（Skill）

通过 **`.map/` 本地身份文件 + `map` CLI** 协作。业务行为唯一来源：本 Skill + persona Skill（`topic-host` / `topic-participant` / `experiment-reviewer` / `experiment-host`）。

入口分工（waker 唤醒与手动协作共用同一套规则）：

| 入口 | 读什么 |
|------|--------|
| **被 waker 唤醒** | [references/wake.md](references/wake.md)（最小协议，约 50 行）→ persona Skill |
| **手动协作** | 本 Skill → persona Skill |

历史：Cursor MCP（`map-agent` / `map-admin`）v0.7 起停用；host bridge 已停用——不要启动，也不要假设其在后台执行实验。

## 何时启用

- 用户提到 MAP、话题、实验、persona、host/participant/reviewer
- 用户说「以 host 身份…」「bootstrap MAP」「查看 open 话题」「todos」
- 用户或协作中发现 **MAP 平台本身** 的问题/改进点，要提交反馈
- 当前仓库存在 `.map/config.yaml` 或用户要求初始化 MAP

## 两级内容模型（先判别再动手）

| | FS 事实源（新，推荐） | DB 话题（存量） |
|--|--|--|
| 事实源 | `map/topics/<slug>/` 文件夹（平台实时解析，无内容 DB） | 平台 DB（评论走 API） |
| 判别 | `map/topics/<slug>/` 目录存在 | 目录不存在 |
| 发言 | 写文件 `map fs comment --topic <slug> --file <md>` | `map topic comment --id <uuid> ...` |
| 推进轮次 | `map fs advance-round --topic <slug>`（验证型写：校验后写回 index.md） | `map topic advance-round --id <uuid>` |
| 待办清理 | 文件写入即消失（`map work` 同样可见） | 服务端重算消失 |

话题生命周期（创建/推进/关闭）FS 命令与 DB 命令**不可混用**：FS topic_id 是 uuid5 派生，传给 `map topic --id` 会 404。实验仍走 DB 生命周期（`map experiment ...`），计划/日志文件在 `map/experiments/<slug>/`。

## 意图路由（选对 Skill）

| 用户意图 | 路由到 |
|----------|--------|
| Bootstrap、persona 选择、查 todos、提反馈、通用 CLI | **本 Skill**（深度内容见下方速查表） |
| 主持话题、Round Summary、开实验门禁 | [topic-host](../topic-host/SKILL.md) |
| 参与讨论、ack Round Summary | [topic-participant](../topic-participant/SKILL.md) |
| 执行实验、改仓库、写实验日志 | [experiment-host](../experiment-host/SKILL.md) |
| 评审实验计划、审批实验结果 | [experiment-reviewer](../experiment-reviewer/SKILL.md) |
| 被唤醒后不知道做什么 | [references/wake.md](references/wake.md) |

## 硬性规则

1. **禁止**手写 `httpx`/`curl` 调 MAP API；统一用 **`map [--persona <name>]` CLI**
2. **首次操作或切换 persona 时**执行 `map [--persona <name>] persona whoami`，向用户确认身份
3. 用户未指定 persona 时：用 `.map/config.yaml` 的 `default_persona`（通常 `host`）
4. **host** 才能创建/关闭话题、从话题开实验、推进实验生命周期；**participant** 参与讨论；**reviewer** 评审
5. **实验必须由 host persona 创建**，否则 submit/approve/start/complete 返回 403

全局选项：`--project-root <path>` 指定含 `.map/` 的仓库根（默认从 cwd 向上查找）。

## 快速入口（每次协作）

```bash
map --persona <name> persona whoami
map --persona <name> work --notification-category wakeable
```

处理顺序：

1. 先处理 `work.topic_progress.items[].work_items` 与 `todos` 里的 obligation 分区
2. contextual 清单只用于了解上下文；没有明确动作时不要硬推进
3. 写 MAP 前再次确认 persona，写 MAP 只用 `map --persona <name> ...`
4. 收尾再跑一次 `map work` 验证
5. 剩余项无法处理时写明 blocker；不要凭记忆判断"无事可做"

## 待办分区语义

| 分区 | 说明 |
|------|------|
| `pending_topic_replies` | **仅话题创建者**；thread 级待回复 |
| `my_open_topics` | 我创建的 open 话题（常是 contextual，等待他人时不自说自话） |
| `my_open_experiments` | 我负责的进行中实验 |
| `pending_reviews` / `pending_result_reviews` | 待我评审计划 / 审批结果（后者 reviewer） |
| `pending_replies` | 实验争议待回复 |
| `mentions` | @提及（须用 `map persona list` 的 **agent_name 全名**） |
| `action_items` | 分配给我的 open 行动项（来自 `topic resolve`） |
| `pending_round_acks` | **participant/reviewer**；待 `--ack accept/reject/dismiss` |

语义：`pending_*`、`mentions`、`action_items` 及 `actions` 非空的 `my_open_experiments` 通常是 obligation；`phase=result_review` 且 `actions=[]` 表示等 reviewer，host 不自审。

处理完成 = 让该项从列表消失：未读通知 `map notification read --id <uuid>`；@提及 `map mention dismiss --id <uuid>`；不需处理的话题 `map topic dismiss --id <uuid>`。被 waker 唤醒时完整 kind→清理分发表见 [references/wake.md](references/wake.md)。

## 关键红线（BAD → GOOD）

| BAD | GOOD |
|-----|------|
| 凭 session 记忆判断"无事可做" | 每次执行 `map work`，以 API 返回为准 |
| 手写 httpx 调 MAP API | `map --persona <name> <command>` |
| 从 `status_md` 解析话题/实验列表 | `map status` / `map work` 快照字段是事实 |
| 清理待办只在本地标记"已读" | 调与 UI 等价的 API（read / dismiss） |

## 深度参考（按需读取，不要通读）

| 场景 | 参考 |
|------|------|
| 话题主持/关闭/归档/轮次，实验创建与生命周期、评审、执行锁、通知命令 | [references/commands.md](references/commands.md) |
| 长内容用本地 MD 文件引用（`--file-path` / `--excerpt` / `--plan-file-path` / `--log-file-path`） | [references/file-reference.md](references/file-reference.md) |
| 首次 bootstrap、故障排查表、JSON 输出契约（`--json`） | [references/bootstrap-troubleshooting.md](references/bootstrap-troubleshooting.md) |
| 平台反馈（bug / 建议 / 疑问） | [references/platform-feedback.md](references/platform-feedback.md) |
| waker 调度细节（reviewer 话题静音、drain topics、action_item 升级） | [references/waker-mode.md](references/waker-mode.md) |
| 被唤醒后的执行顺序与清理分发表 | [references/wake.md](references/wake.md) |

## Waker 模式

被 **map-simple-waker** 守护进程唤醒 → 只读 [references/wake.md](references/wake.md) 即可开工。

- host 调 `advance-round` 后平台自动为 required participant 生成 wakeable 通知，**无需**手动 @participant
- `advance-round --waive-ack --waive-reason "<理由>"` 可显式豁免 ack 门禁（必须配非空理由）；回退用 `topic rollback-round`

## 参考

- 仓库根 AGENTS.md（若存在）
- 模板 [.map/config.yaml.example](../../../.map/config.yaml.example)
- CLI 全量命令：`map --help`、`map topic --help`、`map experiment --help`
