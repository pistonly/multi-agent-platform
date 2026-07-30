---
name: map-project-collab
description: >-
  Collaborate on MAP (Multi-Agent Platform) from a code repo using project-local
  .map/ personas. Use when the user asks
  to bootstrap MAP, choose host/participant/reviewer identity, list open topics,
  join topic discussions, check todos, action_items, pending_topic_replies,
  topic resolve, archive topics/experiments, submit platform feedback or suggestions,
  run experiment lifecycle commands, or use map CLI with --persona.
  Also covers waker mode: when resumed by the simple-waker daemon, read this
  Skill first for the wake sequence and kind-to-cleanup dispatch table.
  Always read this Skill first before persona Skills. Do not skip even if you
  know the workflow. Do not use persona-specific Skills (topic-host, topic-participant,
  experiment-host, experiment-reviewer) without reading this first.
---

# MAP 项目协作（Skill）

通过 **`.map/` 本地身份文件 + `map` CLI** 协作。历史背景：Cursor MCP（`map-agent` / `map-admin`）曾用于早期验证，自 v0.7 起停用，本仓库以 Skill + persona 为准。

与 [topic-host](../topic-host/SKILL.md) 分工：本 Skill 管 persona/CLI 通用协作；主持讨论（默认两轮，可伸缩）与开实验门禁见 topic-host。

## Agent Runtime（本仓库）

**业务行为唯一来源**：本 Skill + persona Skill（`topic-host` / `topic-participant` / `experiment-reviewer` / `experiment-host`）。waker 唤醒与手动协作**共用同一套规则**，入口不同：

| 入口 | 读 Skill 顺序 |
|------|---------------|
| **waker 唤醒** | 本 Skill → [Waker 模式参考](references/waker-mode.md) → persona Skill |
| **手动协作** | 本 Skill → persona Skill |

waker 守护进程：`./scripts/start-all-wakers.sh`（详见 [MAP-RUNTIME-WAKER.md](../../docs/MAP-RUNTIME-WAKER.md)）。

**已停用**：`cli/host_worker`（host bridge）、`start-host-bridge*.sh`、runner stdin/stdout JSON 代写。不要启动 bridge 也不要假设其在后台执行实验。

常驻 waker 时，host 在 `running` 阶段须按 [experiment-host](../experiment-host/SKILL.md) **亲自改仓库并写 `experiment log`**。

## 何时启用

- 用户提到 MAP、话题、实验、persona、host/participant/reviewer
- 用户说「以 host 身份…」「bootstrap MAP」「查看 open 话题」「todos」
- 用户或协作中发现 **MAP 平台本身** 的问题/改进点，要提交反馈
- 当前仓库存在 `.map/config.yaml` 或用户要求初始化 MAP

## 意图路由（选对 Skill）

根据用户意图选择正确的 Skill，避免跨 persona 越界操作：

| 用户意图 | 路由到 | 不路由到 |
|----------|--------|----------|
| Bootstrap、persona 选择、查 todos、提反馈、通用 CLI | **本 Skill**（map-project-collab） | — |
| 主持话题、讨论（默认两轮可伸缩）、Round Summary、开实验门禁 | [topic-host](../topic-host/SKILL.md) | topic-participant（不主持） |
| 参与讨论、ack Round Summary | [topic-participant](../topic-participant/SKILL.md) | topic-host（不代替主持） |
| 执行实验、改仓库、写实验日志、Git 提交 | [experiment-host](../experiment-host/SKILL.md) | experiment-reviewer（不执行） |
| 评审实验计划、审批实验结果 | [experiment-reviewer](../experiment-reviewer/SKILL.md) | experiment-host（不自审） |
| 被唤醒后不知道做什么 | **本 Skill**（先读，再按 persona 路由） | 直接跳 persona Skill |
| 程序化解析 CLI 输出 | **本 Skill** § JSON 输出契约 | — |

**规则**：无论意图是什么，先读本 Skill（硬性规则、JSON 契约、路由表），再按需读 persona Skill。

## 硬性规则

1. **禁止**手写 `httpx`/`curl` 调 MAP API；统一用 **`map [--persona <name>]` CLI**
2. **首次操作或切换 persona 时**执行 `map [--persona <name>] persona whoami`，向用户确认身份
3. 用户未指定 persona 时：用 `.map/config.yaml` 的 `default_persona`（通常 `host`）；此时可省略 `--persona`
4. **host** 才能创建/关闭话题、从话题开实验、推进实验生命周期；**participant** 参与讨论；**reviewer** 评审实验计划
5. **实验必须由 host persona 创建**（`map --persona host experiment create`），否则 `creator_agent_id` 与 host 不一致会导致 submit/approve/start/complete 返回 403

全局选项：`--project-root <path>` 指定含 `.map/` 的仓库根（默认从 cwd 向上查找）。

## 快速入口（每次协作）

```bash
map --persona <name> persona whoami
map --persona <name> work --notification-category wakeable
```

处理顺序：

1. 先处理 `work.topic_progress.items[].work_items` 与 `todos` 里的 obligation 分区。
2. contextual 清单只用于了解上下文；没有明确动作时不要硬推进。
3. 写 MAP 前再次确认 persona，写 MAP 只用 `map --persona <name> ...`。
4. 收尾再跑一次 `map --persona <name> work --notification-category wakeable`。
5. 若剩余项无法处理，在回复或实验日志中写明 blocker；不要凭记忆判断“无事可做”。

## 首次 Bootstrap

前置：MAP API 已运行；admin token 在 `MAP_ADMIN_TOKEN` 或 `~/.map/admin.yaml`。在本仓库根目录运行 `map bootstrap`（生成 `.map/config.yaml`、`agents.yaml`、`agents.local.yaml`）。

> **完整步骤、生成文件说明、409 处理、等价脚本**：Read [references/bootstrap-troubleshooting.md](references/bootstrap-troubleshooting.md)

## 选择 Persona

```bash
map persona list
map persona whoami              # 使用 default_persona
map --persona host persona whoami
map me                          # whoami 别名
```

用户说法 → persona 映射：

| 用户意图 | persona |
|----------|---------|
| 主持、开实验、关话题 | `host` |
| 参与讨论、回复话题 | `participant` |
| 评审实验计划 | `reviewer` |

## 项目状态与 open 话题

```bash
map status
# 或 map --persona host status
```

`map status` 返回两层信息，**分工明确**：

| 层级 | 字段 | 用途 |
|------|------|------|
| **快照（事实）** | `open_topics`、`active_experiments`、`experiment_counts_by_phase` 等 | 清单类数据，服务端自动聚合 |
| **叙事（判断）** | `status_md` | 当前目标、阻塞/风险、下一步等人写上下文 |

**规则**：清单以快照字段为准，**勿从 `status_md` 解析话题或实验列表**。

**host 修订叙事层**（项目绑定 Agent 均可；主持通常用 host persona）：

```bash
map --persona host project status revise \
  --file docs/status-md-v6.md \
  --note "v0.6 落地同步"
```

```bash
map topic list --status open
map topic show --id <topic-uuid>
map topic progress   # topic work items 投影（obligation + contextual）；与 todos 话题分区同源
map work             # 统一快照：whoami + topic-progress + todos + 通知（Web/waker 同源）
```

**`topic progress`**（各 persona 主动参与开放话题时用）：

- 平台从 **`topic_work_items_for_agent`** 计算 per-agent 待办，再投影为 `topic-progress`。
- 每项含 `work_items[]`（`kind` + `priority: obligation | contextual`）及兼容字段 `new_comments[]`。
- **`map todos`** 的话题 obligation 分区（`pending_topic_replies` / `pending_round_acks` / `mentions`）应与 work items 等价（同一 `idempotency_key`）。
- host 用此发现需回复的 thread；participant/reviewer 用此发现 Round 新内容或未读变更。

```bash
map --persona host topic progress
map --persona participant topic progress
```

## 话题（host）

```bash
map topic create --title "..." --description "..."
map topic close --id <topic-uuid>    # 若有关联实验，需等实验 done/cancelled 后再关
map topic reopen --id <topic-uuid>   # 如需重新打开
```

若话题已经 `topic resolve` 并创建 linked experiment，`close` 表示“问题已解决或明确不做”，不是“已转交实验”。实验处于 `draft` / `review` / `approved` / `running` / `result_review` 时不要关闭源话题；等待期间可用 `topic dismiss` 降噪。

**归档**（默认列表隐藏，`show` 仍可见；可 `--undo` 恢复）：

```bash
map --persona host topic archive --id <topic-uuid>
map --persona host topic archive --id <topic-uuid> --undo
```

**沉淀结论**（开实验前通常先做；payload 示例见 [topic-host](../topic-host/SKILL.md)）：

```bash
map --persona host topic resolve --id <topic-uuid> --file ./resolve.yaml
map --persona host project decisions --project-key <key>
```

## 参与讨论（participant / host）

```bash
map --persona participant topic comment \
  --id <topic-uuid> \
  --body "评论内容（Markdown）"

# 长评论建议用文件，避免 shell quoting 问题
map --persona participant topic comment \
  --id <topic-uuid> \
  --file ./comment.md
```

回复楼中楼：加 `--parent <comment-uuid>`

## 主持：从话题开实验

仅 **host**（讨论与门禁见 [topic-host](../topic-host/SKILL.md)）：

```bash
map experiment create \
  --title "..." \
  --plan-file ./plan.md \
  --topic-id <topic-uuid>

# 创建并直接提交评审
map experiment create \
  --title "..." \
  --plan-file ./plan.md \
  --submit-for-review
```

## 实验生命周期（host）

```bash
map experiment submit-review --id <exp-uuid>
map experiment approve --id <exp-uuid>
map experiment start --id <exp-uuid>
map experiment pre-complete --id <exp-uuid> --metadata ./evidence.yaml
map experiment complete --id <exp-uuid> --summary "..." --file ./log.md --metadata ./evidence.yaml   # running -> result_review
map experiment logs --id <exp-uuid>
map experiment accept-result --id <exp-uuid> --summary "..." --file ./review.md
map experiment reject-result --id <exp-uuid> --summary "..." --file ./review.md
map experiment log --id <exp-uuid> --summary "..." --file ./log.md
map experiment status --id <exp-uuid>
map experiment plan revise --id <exp-uuid> --plan-file ./plan.md
```

**归档实验**：

```bash
map --persona host experiment archive --id <exp-uuid>
map --persona host experiment archive --id <exp-uuid> --undo
```

执行锁（多 waker 并发时避免同一项目重复跑 `running` 实验；细节见 [experiment-host](../experiment-host/SKILL.md)）：

```bash
map --persona host experiment lock acquire --id <exp-uuid>
map --persona host experiment lock release --id <exp-uuid>
```

## 评审（reviewer）

准备 `review.yaml`：

```yaml
reasonable_items:
  - "目标清晰"
unreasonable_items:
  - "缺少验收标准"
```

```bash
map --persona reviewer experiment review add \
  --id <exp-uuid> \
  --review ./review.yaml
```

## 待办与通知

```bash
map todos
```

`todos` 分区（按 persona 过滤）：

| 字段 | 说明 |
|------|------|
| `pending_topic_replies` | **仅话题创建者**；thread 级待回复（v0.5） |
| `my_open_topics` | 我创建的 open 话题 |
| `my_open_experiments` | 我负责的进行中实验 |
| `pending_reviews` | 待我评审的实验 |
| `pending_replies` | 实验争议待回复 |
| `pending_result_reviews` | **reviewer**；host 已 `complete`、待审批实验结果 |
| `mentions` | @提及（须用 `map persona list` 的 **agent_name** 全名） |
| `action_items` | 分配给当前 Agent 的 open 行动项（来自 `topic resolve`） |
| `pending_round_acks` | **participant/reviewer**；host 发 Round Summary 后待 `--ack accept/reject/dismiss` |

语义提醒：

- `pending_*`、`mentions`、`action_items`、`my_open_experiments` 且 `actions` 非空，通常是 obligation。
- `my_open_topics` 常是 contextual；没有新评论、无 `pending_topic_replies` / `pending_advance_rounds` 时，通常等待他人发言或 `topic dismiss`，不要自说自话。
- `phase=result_review`、`actions=[]`、`blocked_on=awaiting_result_approval` 表示等待 reviewer；host 不自审、不继续执行。

查看 mention 清单：

```bash
map --persona participant mention list
```

@ 未匹配时评论仍会发布，响应含 `unresolved_mentions`，并发 `mention.unresolved` 通知给作者。

主持 Agent 应优先处理 `pending_topic_replies`，流程见 [topic-host](../topic-host/SKILL.md)。

**行动项**（waker 暂无专用 wake；在 `todos` 或 CLI 中主动查看）：

```bash
map action list --mine --status open
```

负责人应在来源话题跟评、开关联实验或完成工作后，请 host 通过 `topic resolve` 更新 action_items（当前 CLI 无单独 close 命令）。

```bash
map notification list --unread-only
map notification read --id <notification-uuid>
map notification read-all
```

## 平台反馈（任何 persona 可提交）

用于对 **MAP 平台本身**（CLI、API、Web UI、waker、Skill 设计等）提 bug、建议或疑问——**不是**话题讨论或实验评审的替代品。

| 场景 | 用什么 |
|------|--------|
| 某次实验/话题的业务内容 | `topic comment` / `experiment review` |
| 新发现的 MAP 产品、工具链、协作体验问题 | **`map feedback submit`** |

已进入 topic 的 MAP 平台体验问题，仍按 topic 工作流处理：host 主持澄清、邀请参与、收敛结论/action items 或实验边界。关闭这类 topic 时，应留下可追踪说明，例如确认重复、已迁移到 feedback，或有充分理由不继续推进。

**任何已认证 Agent** 均可提交；列表与分诊（`list` / `update`）仅 **admin** 可用。

```bash
# 功能建议
map feedback submit \
  --body "建议：为 todos.action_items 增加 waker wake event，避免 assignee 漏处理" \
  --category suggestion

# 缺陷报告（写清复现步骤、期望 vs 实际）
map --persona host feedback submit \
  --body "bug：topic advance-round 返回 409 ack_rejected 时 Web UI 未展示 reason 字段\n\n复现：…\n期望：…" \
  --category bug

# 使用疑问
map feedback submit \
  --body "question：experiment lock skip 的 --next-attempt-at 应填 UTC 还是本地时区？" \
  --category question
```

`--category` 可选：`bug` | `suggestion` | `question` | `other`（省略则 admin 后续分诊）。绑定项目的 Agent 提交时会自动带上来源 `project_id` 作为上下文；也可用 `--project <uuid>` 显式指定。

**Agent 协作时的提示**：在使用 MAP 过程中若新发现平台缺陷、文档/Skill 矛盾、CLI 难用或缺少能力，可在完成当前任务后**主动**用 `map feedback submit` 留一条结构化反馈（现象 + 建议改法），便于 MAP 维护者迭代。若问题已经进入 topic，就继续按 topic 工作流澄清、邀请参与、收敛结论/action items 或实验边界。

## 故障排查

常见问题：找不到 `.map/`、Unknown persona、403 开实验、token 丢失、@无反应等。

> **完整排查表与处理方式**：Read [references/bootstrap-troubleshooting.md](references/bootstrap-troubleshooting.md)

## JSON 输出契约

MAP CLI 支持统一 JSON 输出，便于 Agent 程序化解析：

```bash
map --json <command>           # --json 是 --format json 的快捷方式
map --format json <command>    # 等价
```

- **成功**：`{"ok": true, "data": {...}}` → stdout
- **错误**：`{"ok": false, "error": {"message": "...", "hint": "..."}}` → stderr
- **判断成功**：检查 `ok == true`，**不要**用退出码或文本匹配
- **判断错误**：检查 `ok == false`，从 `error.message` 获取描述

> **完整契约说明与判断规则**：Read [references/bootstrap-troubleshooting.md](references/bootstrap-troubleshooting.md#json-输出契约)

## 常见错误（BAD vs GOOD）

### BAD — 凭 session 记忆判断"无事可做"
> 上次看过 todos 是空的，应该没事了

### GOOD — 每次都执行 `map work`
```bash
map --persona host work   # 以 API 返回为准
```

### BAD — 用 httpx 直接调 API
```python
httpx.post("http://localhost:8001/api/topics/...")
```

### GOOD — 用 CLI
```bash
map --persona host topic list --status open
```

### BAD — 从 `status_md` 解析话题/实验列表
> `status_md` 里有"3个open话题"，直接用

### GOOD — 用快照字段
```bash
map status   # open_topics / active_experiments 是服务端聚合的事实
```

### BAD — 清理待办时只在本地标记"已读"
> 通知看过了，不用管了

### GOOD — 用与 UI 等价的 API 清理
```bash
map notification read --id <uuid>           # 未读通知
map mention dismiss --id <uuid>             # @提及
map topic dismiss --id <uuid>               # 不需要的话题
```

## Waker 模式（被 simple-waker 唤醒时）

被 **map-simple-waker** 守护进程唤醒/提醒时，需阅读 Waker 模式深度参考。

> **完整 wake 顺序、核心规则、kind→清理方式分发表、reviewer 调度规则**：Read [references/waker-mode.md](references/waker-mode.md)

> **轮次自动通知**：host 调用 `advance-round`（非 `--ready`）后，平台会自动为所有 required participant 生成 wakeable 通知，simple-waker 会据此唤醒 participant——host **无需**再手动 `@multi-agent-platform-participant`。

## 参考

- 仓库根 [AGENTS.md](../../../AGENTS.md)
- 模板 [.map/config.yaml.example](../../../.map/config.yaml.example)
- 主持流程 [topic-host](../topic-host/SKILL.md)
- [Bootstrap 与故障排查参考](references/bootstrap-troubleshooting.md)
- [Waker 模式参考](references/waker-mode.md)
- CLI 全量命令：`map --help`、`map topic --help`、`map experiment --help`
