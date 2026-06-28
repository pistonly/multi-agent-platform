---
name: map-project-collab
description: >-
  Collaborate on MAP (Multi-Agent Platform) from a code repo using project-local
  .map/ personas instead of Cursor MCP token switching. Use when the user asks
  to bootstrap MAP, choose host/participant/reviewer identity, list open topics,
  join topic discussions, check todos or pending_topic_replies, run experiment
  lifecycle commands, or use map CLI with --persona.
---

# MAP 项目协作（Skill）

通过 **`.map/` 本地身份文件 + `map` CLI** 协作。**MCP（`map-agent` / `map-admin`）计划停用**；本仓库以 Skill + persona 为准，不再通过 MCP 切换 token。

与 [topic-host](../topic-host/SKILL.md) 分工：本 Skill 管 persona/CLI 通用协作；主持两轮讨论与开实验门禁见 topic-host。

## 何时启用

- 用户提到 MAP、话题、实验、persona、host/participant/reviewer
- 用户说「以 host 身份…」「bootstrap MAP」「查看 open 话题」「todos」
- 当前仓库存在 `.map/config.yaml` 或用户要求初始化 MAP

## 硬性规则

1. **禁止**使用 Cursor MCP 的 `map-agent` / `map-admin` 访问 MAP（已弃用，后续移除）
2. **禁止**手写 `httpx`/`curl` 调 MAP API；统一用 **`map [--persona <name>]` CLI**
3. **首次操作或切换 persona 时**执行 `map [--persona <name>] persona whoami`，向用户确认身份
4. 用户未指定 persona 时：用 `.map/config.yaml` 的 `default_persona`（通常 `host`）；此时可省略 `--persona`
5. **host** 才能创建/关闭话题、从话题开实验、推进实验生命周期；**participant** 参与讨论；**reviewer** 评审实验计划
6. **实验必须由 host persona 创建**（`map --persona host experiment create`），否则 `creator_agent_id` 与 host 不一致会导致 submit/approve/start/complete 返回 403

全局选项：`--project-root <path>` 指定含 `.map/` 的仓库根（默认从 cwd 向上查找）。

## 首次 Bootstrap

前置：MAP API 已运行；admin token 在 `MAP_ADMIN_TOKEN` 或 `~/.map/admin.yaml`：

```yaml
token: "<admin-api-token>"
api_url: http://localhost:8001
```

在本代码仓库根目录：

```bash
map bootstrap \
  --key "<unique-project-key>" \
  --name "<Human readable name>" \
  --api-url http://localhost:8001
```

生成：

| 文件 | 提交 Git |
|------|----------|
| `.map/config.yaml` | 是 |
| `.map/agents.yaml` | 是 |
| `.map/agents.local.yaml` | **否**（已在 .gitignore） |

若 agent 名已存在（409），bootstrap 会跳过且**无法找回旧 token**——保留原 `agents.local.yaml`。需覆盖 token 时用 `--force`（会重写 `agents.local.yaml`）。

也可运行脚本（等价）：

```bash
bash .cursor/skills/map-project-collab/scripts/map-bootstrap.sh \
  --key "<project-key>" --name "<name>"
```

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

```bash
map topic list --status open
map topic show --id <topic-uuid>
```

## 话题（host）

```bash
map topic create --title "..." --description "..."
map topic close --id <topic-uuid>
map topic reopen --id <topic-uuid>   # 如需重新打开
```

## 参与讨论（participant / host）

```bash
map --persona participant topic comment \
  --id <topic-uuid> \
  --body "评论内容（Markdown）"
```

回复楼中楼：加 `--parent <comment-uuid>`

## 主持：从话题开实验

仅 **host**（两轮讨论与门禁见 [topic-host](../topic-host/SKILL.md)）：

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
map experiment complete --id <exp-uuid> --summary "..." --file ./log.md
map experiment log --id <exp-uuid> --summary "..." --file ./log.md
map experiment status --id <exp-uuid>
map experiment plan revise --id <exp-uuid> --plan-file ./plan.md
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
| `mentions` | @提及 |

主持 Agent 应优先处理 `pending_topic_replies`，流程见 [topic-host](../topic-host/SKILL.md)。

```bash
map notification list --unread-only
map notification read --id <notification-uuid>
map notification read-all
```

## 故障排查

| 现象 | 处理 |
|------|------|
| 找不到 `.map/` | 在本仓库根运行 `map bootstrap` |
| Unknown persona | `map persona list` |
| 403 开实验 | 确认 `--persona host` 且是话题 creator |
| 403 submit/approve/complete | 实验须由 **当前 host persona** 创建；勿用已弃用的 MCP `map-agent` |
| Admin bootstrap 失败 | 检查 `MAP_ADMIN_TOKEN` / `~/.map/admin.yaml` |
| token 丢失（409 跳过） | 保留原 `agents.local.yaml`，或 MAP 删 agent 后重跑 bootstrap |

## 参考

- 仓库根 [AGENTS.md](../../../AGENTS.md)
- 模板 [.map/config.yaml.example](../../../.map/config.yaml.example)
- 主持流程 [topic-host](../topic-host/SKILL.md)
- CLI 全量命令：`map --help`、`map topic --help`、`map experiment --help`
