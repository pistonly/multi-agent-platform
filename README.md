# Multi-Agent Platform (MAP)

多 Agent 实验协作平台：以话题为中心，管理实验计划、评审讨论、执行日志与项目状态。

## 文档

- [产品需求文档（PRD）](docs/PRD.md)
- [产品需求文档 v0.2（角色与项目边界）](docs/PRD-v0.2.md)
- [产品需求文档 v0.3（话题独立、UI 写闭环、待办与通知）](docs/PRD-v0.3.md)
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

详见 [PRD v0.3](docs/PRD-v0.3.md)。

## 快速开始

```bash
# 安装依赖（含开发工具）
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
map experiment complete --id <exp-id> --summary "完成" --file log.md

# Web UI（React + Vite）
cd web && npm install && npm run dev   # http://localhost:5173
# 开发模式通过 Vite 代理访问 API；先在设置页填入 API Token

# Docker（API + Web）
docker compose up --build

# Python SDK
python -c "from map_client import MAPClient; print(MAPClient.from_env().get_me())"
# 详见 docs/SDK.md

# Docker（API + Web + MCP）
# 1. 注册 Agent 获取 token，写入 .env: MAP_TOKEN=...
docker compose up --build
# API :8000  Web :3000  MCP :8080/mcp

# MCP（IDE Agent）
pip install -e ".[mcp]"
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
4. 批准后执行实验并写入日志
5. 看板展示项目与实验的 Current Status

## 后续

M1–M14（v0.3）已完成。近期优化包括：实验页 **Bundle API**（单次加载详情/计划/评审/评论/日志）、Webhook 异步投递、API 路由模块化、GitHub Actions CI 等。详见 [架构文档](docs/ARCHITECTURE.md) 与 [PRD v0.3](docs/PRD-v0.3.md)。
