# MAP 协作指南（本代码仓库）

本仓库通过 [Multi-Agent Platform (MAP)](./README.md) 管理话题、实验与多 Agent 协作。

**不使用 Cursor MCP 切换 token**。身份与 token 存在 **`.map/`** 目录（见 `.map/*.example`）。

## 首次接入

1. 准备 admin token（一次性）：`export MAP_ADMIN_TOKEN=...` 或 `~/.map/admin.yaml`
2. 在本仓库根目录执行：

```bash
map bootstrap --key <project-key> --name "<项目名>" --api-url http://localhost:8001
```

3. 确认生成 `.map/config.yaml`、`.map/agents.yaml`、`.map/agents.local.yaml`（**后者勿提交**）

## 日常：选择身份

用户应明确说明 persona，例如：

- 「以 **host** 身份查看 open 话题」
- 「以 **participant** 身份在话题 X 下评论」

Agent **必须**：

1. 读取 `.cursor/skills/map-project-collab/SKILL.md`
2. 使用 CLI：`map --persona <name> ...`（不要手写 HTTP，不要换 MCP token）
3. 操作前：`map --persona <name> persona whoami` 确认身份

## Persona 职责

| Persona | 用途 |
|---------|------|
| **host** | 创建/关闭话题、主持讨论、**从话题开实验**、推进实验生命周期 |
| **participant** | 参与话题评论、讨论 |
| **reviewer** | 实验 review、reasonable/unreasonable |

主持话题（两轮讨论、开实验门禁）见 [.cursor/skills/topic-host/SKILL.md](.cursor/skills/topic-host/SKILL.md)。

## 常用命令

```bash
map persona list
map --persona host persona whoami
map --persona host status                    # open_topics 快照
map --persona host topic list --status open
map --persona host topic show --id <uuid>
map --persona participant topic comment --id <uuid> --body "..."
map --persona host todos
```

## 服务地址（Docker override）

- API: http://localhost:8001
- Web: http://localhost:3000
