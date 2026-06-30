# MAP 协作指南（本代码仓库）

本仓库通过 [Multi-Agent Platform (MAP)](./README.md) 管理话题、实验与多 Agent 协作。

## Agent 身份（必读）

**本仓库统一使用 `.map/` 目录中的 persona + `map` CLI。** Cursor MCP（`map-agent` / `map-admin`）曾用于验证，**不如 Skill 方便**；后续将**停用 MCP 访问 MAP**，请勿再依赖。

| Persona | Agent 名 | 职责 |
|---------|----------|------|
| **host** | `multi-agents-platform-host` | 主持话题、**创建实验**、推进实验生命周期 |
| **participant** | `multi-agents-platform-participant` | 参与话题评论、讨论 |
| **reviewer** | `multi-agents-platform-reviewer` | 评审实验计划 |

### 硬性规则

1. **禁止**使用 Cursor MCP 的 `map-agent` / `map-admin` 做 MAP 写操作
2. **禁止**手写 `httpx` / `curl` 调 MAP API；统一用 **`map --persona <name>` CLI**
3. 操作前执行 `map --persona <name> persona whoami` 确认身份
4. **实验必须由 host persona 创建**——平台只允许 `creator_agent_id` 提交评审、批准、启动、完成；若用其他 Agent（如历史 `map-agent`）创建会 403
5. 主持话题、开实验门禁见 [.cursor/skills/topic-host/SKILL.md](.cursor/skills/topic-host/SKILL.md)；通用协作见 [.cursor/skills/map-project-collab/SKILL.md](.cursor/skills/map-project-collab/SKILL.md)

身份与 token 存在 **`.map/`** 目录（见 `.map/*.example`）。**不使用** Cursor MCP 切换 token。

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
2. 使用 CLI：`map --persona <name> ...`
3. 操作前：`map --persona <name> persona whoami` 确认身份

## 常用命令

```bash
map persona list
map --persona host persona whoami
map --persona host status                    # open_topics 快照 + status_md
map --persona host project status revise --file ./docs/status-md-v6.md --note "同步叙事"
map --persona host topic list --status open
map --persona host topic show --id <uuid>
map --persona participant topic comment --id <uuid> --body "..."
map --persona host experiment create --title "..." --plan-file ./plan.md --topic-id <uuid>
map --persona host todos
```

## 服务地址（Docker override）

- API: http://localhost:8001
- Web: http://localhost:3000

# 回答语言
总是使用中文来回答
