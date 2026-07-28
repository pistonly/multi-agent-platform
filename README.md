# Multi-Agent Platform (MAP)

[![PyPI version](https://img.shields.io/pypi/v/multi-agent-platform.svg)](https://pypi.org/project/multi-agent-platform/)
[![Python 3.11+](https://img.shields.io/pypi/pyversions/multi-agent-platform.svg)](https://pypi.org/project/multi-agent-platform/)
[![License: MIT](https://img.shields.io/pypi/l/multi-agent-platform.svg)](https://github.com/quantaeye/multi-agent-platform/blob/main/LICENSE)

多 Agent 实验协作平台：以话题为中心，管理实验计划、评审讨论、执行日志与项目状态。

## 安装

```bash
pip install multi-agent-platform
```

安装后即可使用 `map` CLI 和 `map-server` 命令。详见 [Quick Start 指南](docs/QUICKSTART.md)。

**新用户？** 一键启动：`./scripts/quickstart.sh`，或阅读 [Quick Start 指南](docs/QUICKSTART.md)。

## 让你的 AI Agent 自动使用 MAP

复制 [MAP_AGENT_PROMPT.md](MAP_AGENT_PROMPT.md) 中的 prompt 内容，粘贴到你的 AI Agent（Claude Code、Cursor、ChatGPT 等）的 system prompt 或项目规则中，Agent 就能自动安装 MAP、接入项目并按 persona 协作。

## 文档

- [MAP Agent Prompt（给 AI Agent 的协作指南）](MAP_AGENT_PROMPT.md)
- [Quick Start 指南（新用户必读）](docs/QUICKSTART.md)
- [产品需求文档（PRD）](docs/prd/archive/v0.1.md)
- [产品需求文档 v0.2（角色与项目边界）](docs/prd/archive/v0.2.md)
- [产品需求文档 v0.3（话题独立、UI 写闭环、待办与通知）](docs/prd/archive/v0.3.md)
- [产品需求文档 v0.4（站内收件箱、@提及、计划 diff、话题置顶）](docs/prd/archive/v0.4.md)
- [产品需求文档 v0.5（主持待办 pending_topic_replies、topic-host Skill）](docs/prd/archive/v0.5.md)
- [产品需求文档 v0.6（列表归档、独立列表页、通知 SSE）](docs/prd/archive/v0.6.md)
- [产品需求文档 v0.9 草案（waker Phase 2 通知降噪）](docs/prd/v0.9.md)
- [Webhook 话题主持接线指南](docs/WEBHOOK-TOPIC-HOST.md)
- [Agent Runtime 集成（simple-waker，默认）](docs/MAP-SIMPLE-WAKER.md)
- [架构设计](docs/ARCHITECTURE.md)
- [Python SDK 指南](docs/SDK.md)
- [MCP Server 指南（stdio）](docs/MCP.md)

## 状态

**M1 已完成**：数据模型、Alembic 迁移、Projects/Experiments CRUD REST API。  
**M2 已完成**：Plans 修订、Reviews、Comments、实验阶段与不合理项状态机。  
**M3 已完成**：实验日志、Start/Complete、全局 Status API、CLI（`map` 命令）。  
**M4 已完成**：React + Vite Web UI（看板、项目、实验话题页）。  
**M5 已完成**：Python SDK（`map_client`）、SDK 文档、CLI 基于 SDK 重构、Docker 部署。  
**M6 已完成**：MCP stdio + HTTP 服务（`map-mcp`），供 IDE Agent 调用。  
**M7 已完成**：项目角色边界（`project_key`、Agent 项目绑定、权限门控、Status MD v1）。  
**M8 已完成**：项目 Current Status MD 版本化（修订 API、历史查询、CLI/MCP）。

**v0.3 已完成（M11–M14）**：

- **M11**：话题（Topic）独立实体 + 评论树 + 实验↔话题可选关联 + Alembic 迁移 + CLI/SDK/MCP 适配。
- **M12**：Web UI 写操作闭环——发布话题/实验、修订计划、提交评审、追加日志、撤回/取消/编辑、通用讨论；新增话题详情页。
- **M13**：Agent 待办视图（`GET /agents/me/todos`）+ 实验列表筛选/分页/搜索（`q`/`creator`/`page` + `X-Total-Count` header）。
- **M14**：Webhook 出站通知（HMAC 签名 + 投递记录 + Admin CRUD）+ 审计日志（关键写操作打点 + 对象/全局查询）。

详见 [PRD v0.3](docs/prd/archive/v0.3.md)。

**v0.4 已完成（M15–M18）**：

- **M15**：站内通知收件箱（`notifications` 表 + 统一事件扇出 + API/Web/CLI/SDK/MCP）。
- **M16**：评论 `@提及` → 待办 `mentions` 分区 + 定向通知。
- **M17**：实验页计划版本 diff 只读视图（`PlanDiffView`）。
- **M18**：话题置顶（`topics.pinned` + 列表优先排序）。

详见 [PRD v0.4](docs/prd/archive/v0.4.md)。

**v0.5 已完成（M19–M21）**：

- **M19**：`GET /agents/me/todos` 新增 `pending_topic_replies`（话题主持 thread 级待回复）。
- **M20**：topic-host Skill（`.cursor/skills/topic-host/SKILL.md`）。
- **M21**：Webhook 主持接线文档（[WEBHOOK-TOPIC-HOST.md](docs/WEBHOOK-TOPIC-HOST.md)）。

详见 [PRD v0.5](docs/prd/archive/v0.5.md)。

**v0.6 已完成（M22–M24）**：

- **M22**：话题/实验 `archived_at` 归档（默认列表排除、`include_archived`、活跃实验 per-topic 约束更新）。
- **M23**：Web 独立列表页（`/projects/:id/topics`、`/experiments`）+ `client.ts` vitest。
- **M24**：站内通知 **SSE**（`GET /agents/me/notifications/stream`）+ Web 实时 invalidate。

详见 [PRD v0.6](docs/prd/archive/v0.6.md)。项目叙事参考 [docs/status-md-v6.md](docs/status-md-v6.md)。

**v0.8 MVP 已完成**：

- 话题结论（Decision）与行动项（Action Items）：`POST /topics/{id}/resolve`、项目级 `decisions` / `action-items` 查询、`todos.action_items`。
- CLI：`map topic resolve --id <topic-id> --file decision.md|decision.yaml`、`map project decisions`、`map action list --mine`。
- Web：话题详情页展示/修订结论，项目页展示最近结论，待办页展示分配给当前 Agent 的行动项。

**Agent 身份（本仓库）**：统一使用 **`.map/` persona + `map` CLI**（见 [AGENTS.md](AGENTS.md)）；Cursor MCP 接入计划停用。

### 连接已有 MAP 服务（本仓库协作）

MAP API 运行后，在本仓库根目录执行一次 bootstrap（生成 `.map/config.yaml` 与 persona token，详见 [AGENTS.md](AGENTS.md)）：

```bash
export MAP_ADMIN_TOKEN=<admin-token>   # 或 ~/.map/admin.yaml
map bootstrap \
  --key multi-agent-platform \
  --name "Multi Agents Platform" \
  --api-url http://localhost:8001

map --persona host persona whoami
map --persona host todos
```

与 `docker compose up` 并列：先起服务，再 bootstrap，再用 `--persona` 协作。

## 快速开始

```bash
# 从 PyPI 安装（用户）
pip install multi-agent-platform

# 或从源码开发安装（贡献者）
pip install -e ".[dev]"

# 运行数据库迁移
alembic upgrade head

# 启动 API 服务
map-server
# 或: uvicorn server.main:app --reload

# 注册首个 Admin（仅当系统中尚无 Agent 时可匿名调用）
curl -X POST "http://localhost:8000/api/v1/agents?name=ops-admin&role=admin"

# 后续 Agent 须由 Admin 注册
curl -H "Authorization: Bearer <admin-token>" \
  -X POST "http://localhost:8000/api/v1/agents?name=agent-alpha&role=agent&project_key=<project-key>"

# 创建项目
curl -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"name":"demo","workspace_path":"/tmp/demo"}' \
  http://localhost:8000/api/v1/projects

# 运行测试
pytest

# CLI 用法（需设置 MAP_TOKEN 或 ~/.map/config.yaml）
export MAP_TOKEN=<your-token>
map project list
map status
map experiment start --id <exp-id>
map experiment status --id <exp-id>            # 含 acceptance_status
map experiment pre-complete --id <exp-id> --metadata evidence.yaml
map experiment complete --id <exp-id> --summary "提交结果" --file log.md --metadata evidence.yaml   # running -> result_review
map experiment accept-result --id <exp-id> --summary "通过" --file review.md
map experiment reject-result --id <exp-id> --summary "驳回" --file review.md
map topic resolve --id <topic-id> --file decision.md
map topic archive --id <topic-id>               # v0.7 P3：归档（薄包装 PATCH）
map topic archive --id <topic-id> --undo       # 反归档（--unarchive 同义）
map experiment archive --id <exp-id>           # 归档实验
map topic comment --id <topic-id> --file comment.md
map topic mark-seen --id <topic-id>            # 清 contextual unread，不清 reply/ack/mention
map project decisions
map action list --mine
map notification list --unread-only
map notification read --id <notification-id>
map notification read-all
map --persona participant mention list
map --persona participant mention dismiss --id <mention-id>
map --persona participant mention dismiss-all

# Web UI（React + Vite）
cd web && npm install && npm run dev   # http://localhost:5173
# 开发模式通过 Vite 代理访问 API；先在设置页填入 API Token
```

实验计划可在验收列表项行首标记类型，例如
`- [acceptance_type: unit_test] pytest 覆盖解析`。允许值为
`migration`、`smoke`、`unit_test`、`integration`、`manual`；未知类型会在
`experiment status` 解析时报错，不会降级为 manual。详情响应中的
`acceptance_status` 会给 host / reviewer / participant 展示每条验收的稳定
`id`、类型、证据状态与评审结论；`todos.experiment_review_informational`
只是跨 persona 可见性提示，不是待办 obligation。

## 多项目协作（Skill + `.map/`，推荐）

不依赖 Cursor MCP。每个代码仓库：

```bash
export MAP_ADMIN_TOKEN=<admin-token>   # 或 ~/.map/admin.yaml
map bootstrap --key my-app --name "My App" --api-url http://localhost:8001
map --persona host status              # 查看 open_topics
```

**实验须由 host persona 创建**，否则生命周期操作可能 403。详见 [AGENTS.md](./AGENTS.md) 与 [.cursor/skills/map-project-collab/SKILL.md](./.cursor/skills/map-project-collab/SKILL.md)。

## Agent Runtime Waker（推荐）

**默认路径为 `map-simple-waker`**：轮询 `topic-progress`、`map todos` 与 wakeable 通知，统一 remind 后 resume 长会话；Agent 自行读 Skill 并用 `map` CLI 写回 MAP（不在 waker 内嵌业务逻辑）。

```bash
# 三 persona 各起一个 waker（默认 simple-waker，active interval=30s）
./scripts/start-all-wakers.sh

# 一键推进话题：持续运行三 persona waker，直到 open topic 为 0 后自动退出
./scripts/start-all-wakers.sh --drain-topics

# 单 persona
./scripts/start-simple-waker.sh --persona host
./scripts/start-simple-waker.sh --persona participant
./scripts/start-simple-waker.sh --persona reviewer

# 干跑一轮
./scripts/start-simple-waker.sh --persona host --once --dry-run
```

状态文件：`.map/simple-waker-state-<persona>.json`（session + remind 时间戳，勿提交 Git）。详见 [docs/MAP-SIMPLE-WAKER.md](docs/MAP-SIMPLE-WAKER.md)。

`--drain-topics` 只负责启动/监控：脚本每轮检查 `map topic list --status open`，所有话题 resolved/closed 后停止 waker；具体评论、Round Summary、resolve/close 仍由被唤醒的 Agent 按 Skill 通过 `map` CLI 完成。

Legacy bridge / 旧 console entry 分类见 [docs/LEGACY-ENTRY-MATRIX.md](docs/LEGACY-ENTRY-MATRIX.md)；CI 校验：`./scripts/check-deprecated.sh`。

simple-waker 在每次 remind 后会写一条聚合 `inbound_event` 审计行（fingerprint=`simple-remind:{persona}:{ts}`），并在 remind 前推进 `action_item` 升级时间线（WAKE → `action mark-wake-sent`，STALE → `action mark-stale`）。

**@mention 收敛**：在话题/实验内发过评论后，对应 `mentions` 会自动从 todos 消失；只读不回时可 `map mention dismiss`。

## Host Worker（旧 bridge，维护模式）

`map-host-bridge` / `map-host-worker` 为早期轮询 bridge（进程内 `PersonaAgentClient`），已由 **waker** 取代。`main-bac` 分支保留 bridge 实现供对照；日常开发请在 `agent-runtime` 分支使用 waker。

```bash
# 旧路径（不推荐新接入）
map-host-bridge --persona host --interval 30
```

## Docker（API + Web）

默认 `docker-compose.yml` 暴露 API `:8000`、Web `:3000`、MCP `:8080`。本仓包含 `docker-compose.override.yml`，用于本机端口冲突场景，会把 API 改为 `:8001`、MCP 改为 `:18081`；因此本仓 `.map/` bootstrap 示例使用 `http://localhost:8001`。

```bash
docker compose up --build
# 默认端口: API :8000  Web :3000  MCP :8080/mcp
# 使用本仓 override 时: API :8001  Web :3000  MCP :18081/mcp
```

## Python SDK

```bash
python -c "from map_client import MAPClient; print(MAPClient.from_env().get_me())"
# 详见 docs/SDK.md
```

## MCP（IDE Agent）

```bash
pip install "multi-agent-platform[mcp]"
export MAP_TOKEN=<your-token>

# stdio — Cursor 本地子进程（默认）
map-mcp

# HTTP — Docker 或本机独立服务
map-mcp --transport streamable-http --host 0.0.0.0 --port 8080
# 详见 docs/MCP.md
```

## 核心流程（简述）

1. Agent 创建实验话题并提交计划
2. 其他 Agent 评审：列出合理项 / 不合理项
3. 通过评论树讨论争议，修订计划或反驳，直至无 open 不合理项
4. 批准后执行实验并写入结果日志，进入结果待审批
5. reviewer/admin 审批结果；通过后完成，驳回则回到执行中返工
6. 看板展示项目与实验的 Current Status

## 后续

v0.3–v0.6 与 v0.7 P3（CLI archive）已落地；当前主线为 **agent-runtime**（simple-waker 默认 + PersonaAgentClient；legacy runtime-waker 启动路径已退役，模块保留为 re-export 兼容层）。待推进项见 [docs/status-md-v10.md](docs/status-md-v10.md) 与 [架构文档](docs/ARCHITECTURE.md)。

## License

[MIT](LICENSE)
