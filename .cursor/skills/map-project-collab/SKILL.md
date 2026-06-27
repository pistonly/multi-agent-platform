---
name: map-project-collab
description: >-
  Collaborate on MAP (Multi-Agent Platform) from a code repo using project-local
  .map/ personas instead of Cursor MCP token switching. Use when the user asks
  to bootstrap MAP, choose host/participant/reviewer identity, list open topics,
  join topic discussions, or run map CLI with --persona.
---

# MAP 项目协作（Skill）

通过 **`.map/` 本地身份文件 + `map` CLI**，在多代码项目间协作，**无需**为每个项目切换 Cursor MCP token。

## 何时启用

- 用户提到 MAP、话题、实验、persona、host/participant
- 用户说「以 host 身份…」「bootstrap MAP」「查看 open 话题」
- 当前仓库存在 `.map/config.yaml` 或用户要求初始化 MAP

## 硬性规则

1. **禁止**依赖 Cursor MCP 的 map-agent/map-admin 连接（除非用户明确要求 MCP）
2. **禁止**手写 `httpx`/`curl` 调 MAP API；统一用 **`map --persona <name>` CLI**
3. 每次 MAP 操作前执行 **`map --persona <name> persona whoami`**，向用户确认身份
4. 用户未指定 persona 时：默认 **`host`**（见 `.map/config.yaml` 的 `default_persona`）
5. **host** 才能 `topic` 关联开实验；**participant/reviewer** 只讨论与 review

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

若 agent 名已存在（409），bootstrap 会跳过且**无法找回旧 token**——保留原 `agents.local.yaml`。

也可运行脚本（等价）：

```bash
bash .cursor/skills/map-project-collab/scripts/map-bootstrap.sh \
  --key "<project-key>" --name "<name>"
```

## 选择 Persona

```bash
map persona list
map --persona host persona whoami
```

用户说法 → persona 映射：

| 用户意图 | persona |
|----------|---------|
| 主持、开实验、关话题 | `host` |
| 参与讨论、回复话题 | `participant` |
| 评审实验计划 | `reviewer` |

## 查看 open 话题（标准流程）

```bash
map --persona host status
# 读 open_topics 字段（不要用 status_md 猜列表）

map --persona host topic list --status open

map --persona host topic show --id <topic-uuid>
```

## 参与讨论

```bash
map --persona participant topic comment \
  --id <topic-uuid> \
  --body "评论内容（Markdown）"
```

回复楼中楼：加 `--parent <comment-uuid>`

## 主持：从话题开实验

仅 **host**：

```bash
map --persona host experiment create \
  --title "..." \
  --plan-file ./plan.md \
  --topic-id <topic-uuid>
```

## 待办与通知

```bash
map --persona host todos
map --persona participant notification list --unread-only
```

## 故障排查

| 现象 | 处理 |
|------|------|
| 找不到 `.map/` | 在本仓库根运行 `map bootstrap` |
| Unknown persona | `map persona list` |
| 403 开实验 | 确认 `--persona host` 且是话题 creator |
| Admin bootstrap 失败 | 检查 `MAP_ADMIN_TOKEN` / `~/.map/admin.yaml` |

## 参考

- 仓库根 [AGENTS.md](../../AGENTS.md)
- 模板 [.map/config.yaml.example](../../.map/config.yaml.example)
