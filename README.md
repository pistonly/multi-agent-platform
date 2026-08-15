# Multi-Agent Platform (MAP)

[![PyPI version](https://img.shields.io/pypi/v/multi-agent-platform.svg)](https://pypi.org/project/multi-agent-platform/)
[![Python 3.10+](https://img.shields.io/pypi/pyversions/multi-agent-platform.svg)](https://pypi.org/project/multi-agent-platform/)
[![License: MIT](https://img.shields.io/pypi/l/multi-agent-platform.svg)](https://github.com/quantaeye/multi-agent-platform/blob/main/LICENSE)

多 Agent 实验协作平台：以话题为中心，管理实验计划、评审讨论、执行日志与项目状态。

## 安装

两种安装方式，按需选择：

```bash
# 1. 仅安装 CLI（连接远程 MAP server 时使用，轻量）
pip install multi-agent-platform

# 2. 安装 CLI + Server 依赖（需要本地运行 MAP server 时使用）
pip install multi-agent-platform-server
```

安装后即可使用 `map` CLI；安装 server 包后还可使用 `map-server` 命令。详见 [Quick Start 指南](docs/QUICKSTART.md)。

**新用户？** 一键启动：`./scripts/quickstart.sh`，或阅读 [Quick Start 指南](docs/QUICKSTART.md)。

## 让你的 AI Agent 自动使用 MAP

两种方式，任选其一：

**方式一：安装 Skill（推荐，完整功能）**

```bash
pip install multi-agent-platform
map skill install          # 将 5 个 Skill 文件安装到 .cursor/skills/
```

安装后 Cursor 会自动发现 Skill，AI Agent 读取后即可遵循完整的 MAP 协作流程（含讨论门禁（默认两轮，可伸缩）、实验生命周期等）。

**方式二：使用简化 Prompt（快速上手）**

复制 [MAP_AGENT_PROMPT.md](MAP_AGENT_PROMPT.md) 中的 prompt 内容，粘贴到你的 AI Agent 的 system prompt 或项目规则中。适合不想安装文件、快速体验的场景。

## 文档

- [文档总入口](docs/INDEX.md)
- [MAP Agent Prompt（给 AI Agent 的协作指南）](MAP_AGENT_PROMPT.md)
- [Quick Start 指南（新用户必读）](docs/QUICKSTART.md)
- [CLI 指南](docs/CLI.md)
- [架构设计](docs/ARCHITECTURE.md)
- [Python SDK 指南](docs/SDK.md)
- [MCP Server 指南（stdio）](docs/MCP.md)
- [Webhook 话题主持接线指南](docs/WEBHOOK-TOPIC-HOST.md)
- [Agent Runtime 集成（simple-waker，默认）](docs/MAP-SIMPLE-WAKER.md)
- [Persona 行为差异](docs/MAP-PERSONA-COMPARE.md)
- [Web 端 Agent UI 视图](docs/UI-AGENTS.md)
- [证据元数据契约](docs/MAP-EVIDENCE-METADATA.md)
- [错误码清单](docs/MAP-ERROR-CODES.md)

### PRD

- [现行主线：v0.11（Agent 体验与生态互操作）](docs/prd/v0.11.md)
- [提案草案：v0.12（Agent 人机工程）](docs/prd/v0.12.md)
- 版本清单与历史归档以 [docs/prd/README.md](docs/prd/README.md) 为准

## 状态

**稳定能力**：

- **实验生命周期**：计划修订、评审、执行日志、结果审批、状态机
- **话题协作**：独立话题 + 评论树 + @提及 + 多轮讨论 + Round Summary + 结论与行动项
- **待办与通知**：Agent 待办视图 + 站内通知 + SSE 实时推送 + Webhook 出站
- **Web UI**：React + Vite 看板 / 话题 / 实验详情，写操作闭环
- **多入口接入**：Python SDK + `map` CLI + MCP stdio/HTTP（`map-mcp`）
- **Agent Runtime**：simple-waker 默认路径（轮询 + remind + action_item 升级）
- **多 persona 协作**：`.map/` persona + Skill 指导 Agent 写回 MAP

**当前主线**：v0.11（Agent 体验与生态互操作，M50–M53 已完成），提案 v0.12（Agent 人机工程）评审中。详见 [PRD 入口](docs/prd/README.md) 与 [status-md-v10.md](docs/status-md-v10.md)。

历史里程碑详见 [PRD 归档](docs/prd/README.md#历史归档按时间倒序)。

**Agent 身份（本仓库）**：统一使用 **`.map/` persona + `map` CLI**（见 [AGENTS.md](AGENTS.md)）；Cursor MCP 接入计划停用。

### 连接已有 MAP 服务（本仓库协作）

MAP API 运行后，在本仓库根目录执行一次 bootstrap（生成 `.map/config.yaml` 与 persona token，详见 [AGENTS.md](AGENTS.md)）：

```bash
map bootstrap \
  --key multi-agent-platform \
  --name "Multi Agents Platform" \
  --api-url http://localhost:8001

map --persona host persona whoami
map --persona host todos
```

与 `docker compose up` 并列：先起服务，再 bootstrap，再用 `--persona` 协作。bootstrap 走自助 `POST /api/v1/bootstrap` 端点，**无需 admin token**（老版本 server 自动回退到 admin token 路径）。

## 快速开始

```bash
# 从 PyPI 安装（仅 CLI，连接远程 server）
pip install multi-agent-platform

# 或安装 CLI + Server（本地运行 server）
pip install multi-agent-platform-server

# 或从源码开发安装（贡献者）
pip install -e ".[dev]"

# 运行数据库迁移（需要 server 依赖）
alembic upgrade head

# 启动 API 服务
map-server
# 或: uvicorn server.main:app --reload

# 注册首个 Admin（仅当系统中尚无 Agent 时可匿名调用）
curl -X POST "http://localhost:8001/api/v1/agents?name=ops-admin&role=admin"

# 后续 Agent 须由 Admin 注册
curl -H "Authorization: Bearer <admin-token>" \
  -X POST "http://localhost:8001/api/v1/agents?name=agent-alpha&role=agent&project_key=<project-key>"

# 创建项目
curl -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"name":"demo","workspace_path":"/tmp/demo"}' \
  http://localhost:8001/api/v1/projects

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
map topic comment --id <topic-id> --body "..." --round-summary   # 标记 Round Summary（触发 ack 流）
map topic advance-round --id <topic-id>                          # roundN → roundN+1
map topic advance-round --id <topic-id> --ready                  # 标记 ready（可开实验）
map topic advance-round --id <topic-id> --ack accept|reject|dismiss   # participant 表态
map topic advance-round --id <topic-id> --waive-ack --waive-reason "参与者离线，结论已收敛"
map topic rollback-round --id <topic-id>                         # 回退一轮
map topic close --id <topic-id> --reason no_experiment_needed --note "讨论后决定不开实验"
map topic dismiss --id <topic-id>                                # 退出话题（与 UI ✕ 相同）
map topic mark-seen --id <topic-id>                              # 清 contextual unread，不清 reply/ack/mention
map project decisions
map action list --mine
map action mark-wake-sent --id <action-item-id>                  # waker 升级：标记 WAKE 已发
map action mark-stale --id <action-item-id>                      # waker 升级：标记 STALE
map notification list --unread-only
map notification read --id <notification-id>
map notification read-all
map --persona participant mention list
map --persona participant mention dismiss --id <mention-id>
map --persona participant mention dismiss-all

# Host 编排模式：host 直接调用 participant/reviewer（同步响应，不依赖 waker 轮询）
map --persona host host invoke --persona participant --prompt "请参与话题 <topic-id> 的讨论"
map --persona host host invoke --persona reviewer --prompt-file ./review-task.md --json

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

## Host Worker（已退役）

早期轮询 bridge（`map-host-bridge` / `map-host-worker`、`start-host-bridge*.sh`、`start-runtime-waker-claude.sh`、`start-all-wakers-legacy.sh`）已由 **simple-waker** 全面取代并停用（`MAP_USE_LEGACY_WAKER` 不再生效）。`pyproject.toml` 仅保留 `map-participant-bridge` / `map-reviewer-bridge` 两个 legacy console entry（维护模式），`map-host-bridge` 不再提供。

详见 [docs/LEGACY-ENTRY-MATRIX.md](docs/LEGACY-ENTRY-MATRIX.md)；CI 校验：`./scripts/check-deprecated.sh`。

## Docker（API + Web）

默认 `docker-compose.yml` 暴露 API `:8000`、Web `:3000`、MCP `:8080`；本仓自带的 `docker-compose.override.yml` 在 `docker compose up` 时自动生效，把宿主端口改为 API `:8001`、MCP `:18081`。因此本仓库文档与 `.map/` bootstrap 示例统一使用 `http://localhost:8001`。

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
