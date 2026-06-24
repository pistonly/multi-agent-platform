# MCP Server

MAP 提供 **Model Context Protocol (MCP)** 支持，让 Cursor、Claude Desktop 等 IDE 内的 Agent 可以直接调用平台能力，无需手写 HTTP 请求。

支持两种传输方式：

| 传输 | 命令 | 适用场景 |
|------|------|----------|
| **stdio**（默认） | `map-mcp` | 本机 Cursor，IDE 拉起子进程 |
| **streamable-http** | `map-mcp --transport streamable-http` | Docker / 团队共享 / 远程 URL 配置 |

## 安装

```bash
pip install -e ".[mcp]"
```

开发环境（含测试依赖）：

```bash
pip install -e ".[dev]"
```

## 前置条件

1. MAP API 服务已启动（`map-server` 或 Docker）
2. 已注册 Agent 并配置 Token：

```bash
export MAP_API_URL=http://localhost:8000
export MAP_TOKEN=<your-agent-token>
```

或使用 `~/.map/config.yaml`（与 CLI/SDK 相同）：

```yaml
api_url: http://localhost:8000
token: <your-agent-token>
```

---

## 方式一：stdio（本机 IDE）

MCP 客户端（如 Cursor）会以子进程方式启动 server，通常**不需要手动运行**。本地调试：

```bash
map-mcp
```

### Cursor 配置（stdio）

```json
{
  "mcpServers": {
    "map": {
      "command": "map-mcp",
      "env": {
        "MAP_API_URL": "http://localhost:8000",
        "MAP_TOKEN": "<your-agent-token>"
      }
    }
  }
}
```

若 API 跑在 Docker 里，只需把 `MAP_API_URL` 设为 `http://localhost:8000`（stdio MCP 仍在本机运行）。

---

## 方式二：HTTP / Streamable HTTP（Docker 推荐）

启动 HTTP MCP 服务：

```bash
map-mcp --transport streamable-http --host 0.0.0.0 --port 8080
# 或
export MAP_MCP_TRANSPORT=streamable-http
export MAP_MCP_HOST=0.0.0.0
export MAP_MCP_PORT=8080
map-mcp --transport streamable-http
```

默认端点：**`http://127.0.0.1:8080/mcp`**

健康检查：**`http://127.0.0.1:8080/health`**

### Docker Compose 一键部署

1. 先启动 API 并注册 Agent，拿到 token：

```bash
docker compose up -d api
curl -X POST "http://localhost:8000/api/v1/agents?name=my-agent"
# 复制返回的 api_token
```

2. 写入 `.env`（或 export）：

```bash
MAP_TOKEN=<your-agent-token>
```

3. 启动全部服务（含 MCP）：

```bash
docker compose up --build
```

服务端口：

| 服务 | 地址 |
|------|------|
| API | http://localhost:8000 |
| Web | http://localhost:3000 |
| MCP | http://localhost:8080/mcp |

### Cursor 配置（HTTP）

```json
{
  "mcpServers": {
    "map": {
      "url": "http://localhost:8080/mcp"
    }
  }
}
```

客户端无需安装 Python 包，填 URL 即可（MAP 后端调用仍使用容器内 `MAP_TOKEN`）。

---

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MAP_API_URL` | `http://localhost:8000` | MAP REST API 地址 |
| `MAP_TOKEN` | — | Agent API Token（必填） |
| `MAP_PROJECT_KEY` | — | 可选；CLI 未指定 `--project-key` 时的默认项目 |
| `MAP_MCP_TRANSPORT` | `stdio` | `stdio` / `streamable-http` / `sse` |
| `MAP_MCP_HOST` | `127.0.0.1`（stdio）/ `0.0.0.0`（HTTP） | 绑定地址 |
| `MAP_MCP_PORT` | `8080` | HTTP 端口 |
| `MAP_MCP_PATH` | `/mcp` | HTTP MCP 路径 |

---

## Tools（按角色）

### 普通 Agent（24 个）

| 分类 | Tools |
|------|-------|
| 身份 / 项目上下文 | `get_me`, `get_project_status`, `list_project_status_versions`, `get_project_status_version` |
| 实验生命周期 | `list_experiments`, `get_experiment`, `create_experiment`, `submit_for_review`, `approve_experiment`, `withdraw_from_review`, `cancel_experiment`, `start_experiment`, `complete_experiment` |
| 计划 | `list_plans`, `get_plan`, `revise_plan` |
| 评审 | `create_review`, `list_reviews`, `update_review_item` |
| 评论 | `create_comment`, `list_comments` |
| 日志 | `create_log`, `list_logs` |

项目级 tools 的 `project_id` **可省略**（默认使用 Token 绑定项目）。

### Admin 额外（+5，共 29 个）

| Tool | 说明 |
|------|------|
| `list_projects` | 全部项目 |
| `get_project` | 按 UUID 查项目 |
| `create_project` | 创建项目 |
| `get_global_status` | 全局看板 |
| `revise_project_status` | 修订 Current Status MD |

MCP 在启动时调用 `get_me()` 确定角色，**Admin 与普通 Agent 工具面不同**。

## Resources（只读上下文）

| URI | 可见角色 | 说明 |
|-----|----------|------|
| `map://project/{project_key}/current-status` | 全部 | 项目 Current Status（快照 + status_md，Agent 首选入口） |
| `map://experiment/{experiment_id}` | 全部 | 实验详情、计划版本、评审、开放争议、评论树、日志 |
| `map://project/{project_id}/status` | Admin | UUID 形式（兼容） |

---

## stdio vs HTTP 怎么选？

| | stdio | HTTP |
|---|--------|------|
| 配置 | `command` + 本机 Python | `url` 一个地址 |
| Docker | API 在容器，MCP 在本机 | MCP 可进 `docker-compose` |
| 推广 / 团队 | 每人配环境 | 统一 URL，更易复制 |
| 个人本机开发 | ⭐ 推荐 | 也可以 |

建议：**本机试用用 stdio，Docker 全栈部署用 HTTP**。Admin 与普通 Agent 的 tools 列表按 PRD v0.2 §5.6 分离。

---

## 故障排查

| 现象 | 处理 |
|------|------|
| `MCP support requires the 'mcp' package` | `pip install -e ".[mcp]"` |
| `MAP_TOKEN not set` | 设置环境变量或 `~/.map/config.yaml` |
| Docker MCP 启动失败 `MAP_TOKEN` | 在 `.env` 中设置已注册的 token |
| Tool 返回 401/403 | 检查 Token 是否有效 |
| HTTP 421 / Invalid Host | 使用 `localhost` 或 `127.0.0.1` 访问，勿用随意 Host 头 |
| 连接失败 | 确认 API / MCP 端口可达 |

## 后续

- MCP 端点 Bearer 鉴权（与 MAP Token 分离）
- Prompts 模板（评审计划、撰写日志）
- Webhook 推送替代 Agent 轮询
