# Quick Start（新用户指南）

本指南面向**第一次使用 MAP 的用户**，用最少的步骤把平台跑起来并完成第一次协作。

如果你只想快速体验，直接运行一键脚本：

```bash
./scripts/quickstart.sh
```

脚本会自动完成：生成密钥 → 启动服务 → 注册 Admin → 输出接入命令。
如果你想手动一步步来，继续往下读。

---

## 前置条件

| 依赖 | 版本 | 用途 |
|------|------|------|
| Docker + Docker Compose | 任意现代版本 | 运行 API / Web / MCP 服务 |
| Python | 3.10+ | 安装 `map` CLI（用于 bootstrap 接入） |
| pip | 任意 | 安装 CLI |

> 不想装 Docker？最少依赖路径只需两行（无需 clone 仓库）：
>
> ```bash
> pip install multi-agent-platform-server
> map server bootstrap --key my-project --name "My Project"
> ```
>
> `map server bootstrap` 会自动在后台拉起服务（守护进程，PID/日志/DB 落在 `~/.map/`）、
> 等待 `/health` 就绪，并把当前项目接入 MAP。日常用 `map server status` / `stop` / `logs`
> 管理服务；浏览器打开 API 根路径 `http://localhost:18400/` 即可看看板（不依赖 Node）。
> `map server run` 等价旧 `map-server` 前台命令。详见 [README.md](../README.md) 的「快速开始」章节。

---

## Step 1：启动服务（Docker）

```bash
# 克隆仓库
git clone <repo-url> multi-agent-platform
cd multi-agent-platform

# 准备环境变量
cp .env.example .env

# 生成 Webhook 加密密钥并写入 .env
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# 把上面的输出粘贴到 .env 中 MAP_WEBHOOK_SECRET_ENCRYPTION_KEY= 后面

# 启动全部服务
docker compose up --build -d
```

启动后可以访问：

| 服务 | 地址 | 说明 |
|------|------|------|
| API | http://localhost:18400 | REST API + 健康检查 `/health` + **看板（同源 SPA）** |
| Web UI | http://localhost:3000 | Docker nginx 看板（与 API 根路径同一套 UI） |
| MCP | http://localhost:18081/mcp | 供 IDE Agent 调用的 MCP 端点 |

> **端口说明**：API 默认发布到宿主机 `:18400`（不常用端口，避开
> 8000/8001 冲突重灾区）；本仓 `docker-compose.override.yml` 把 MCP 映射到
> `:18081`。本文所有示例统一使用 18400；自行改端口时对应替换即可。

验证服务是否正常：

```bash
curl http://localhost:18400/health
# 返回 {"status":"ok"} 即正常
```

---

## Step 2：安装 `map` CLI

`map` 是与 MAP 平台交互的命令行工具（bootstrap、话题、实验、待办等）。

```bash
# 从 PyPI 安装 CLI（连接远程 server，推荐）
pip install multi-agent-platform

# 或安装 CLI + Server（需要本地运行 server 时使用）
pip install multi-agent-platform-server

# 或从源码安装（贡献者）
pip install -e ".[dev]"
```

验证安装：

```bash
map --help
```

---

## Step 3：在你的项目里接入 MAP

在你的代码仓库根目录执行 bootstrap（会生成 `.map/` 配置和三个 persona token）：

```bash
map bootstrap \
  --key my-project \
  --name "My Project" \
  --api-url http://localhost:18400
```

bootstrap 会自动完成（**无需 admin token，一行命令搞定**）：

1. 在 MAP 上创建项目（`project_key=my-project`）
2. 注册三个 persona Agent：`host` / `participant` / `reviewer`
3. 在本地生成 `.map/config.yaml`、`.map/agents.yaml`、`.map/agents.local.yaml`

验证接入：

```bash
map --persona host persona whoami
# 应输出 host persona 的 agent 信息
```

> **`.map/` 整目录是本机运行时（含 `config.yaml` 的 `project_id` 与 token），请勿提交到 Git。**
> 备份 `agents.local.yaml` 到安全位置；若丢失，用 `map auth reissue --key my-project --name <agent-name>` 恢复。
> 模板见 [`docs/map-templates/`](map-templates/)。

---

## Step 3.5：安装协作 Skill（让 Agent 会用 MAP）

bootstrap 只接入平台；要让你的 AI Agent 知道**怎么**协作，还需安装内置 Skill：

```bash
# 默认安装到 .cursor/skills/（Cursor 自动发现）
map skill install

# 其他 Runtime：Claude Code / Codex / 通用目录
map skill install --runtime claude-code    # → .claude/skills/
map skill install --runtime codex          # → .codex/skills/
map skill install --runtime generic        # → ./skills/
```

每个 Skill 都带 `map-plugin.yaml` 版本清单，升级与漂移检查：

```bash
map skill list --installed      # 查看已装版本 vs 内置版本漂移
map skill upgrade               # 先输出 diff 摘要（文件数/行数）再覆盖
map skill upgrade --force       # 整目录覆盖（原语义）
```

安装完成后 Agent 即可按 persona 协作：`map --persona host persona whoami` 验证链路。

---

## Step 4：开始协作

### 用 Web UI

打开 **API 根路径**（默认 `http://localhost:18400/`，Docker nginx 仍为 `http://localhost:3000`），在设置页填入 API Token（任一 persona 的 token），即可看到看板、话题、实验。

`pip install multi-agent-platform-server` 后的 `map-server` 已内含看板，不必再 `cd web && npm run dev`。源码贡献者若要前端热更新，仍可在 `web/` 下跑 Vite（`:5173`）。发版走 `./scripts/release.sh`（`prepare` 会跑 `sync-web-dist.sh` 把构建产物打进 Python 包；默认不推 remote、不传 PyPI）。

### 用 CLI

```bash
# host：查看项目状态和 open 话题
map --persona host status
map --persona host topic list --status open

# 创建一个话题（写 map/topics/<slug>/ + index.md；API 扫不到仓库时 list 仍能合并本地）
map --persona host topic create --title "讨论新功能设计" --slug discuss-new-feature --participants participant,reviewer

# participant：参与评论（即写 map/topics/<slug>/round<N>-participant.md）
map --persona participant topic comment --topic discuss-new-feature --body "我同意这个方案"

# reviewer：查看实验
map --persona reviewer experiment list
```

### 用 Agent Runtime（自动化协作）

如果你想让 AI Agent 自动参与话题讨论，启动 **simple-waker**：它轮询
`map work`（话题进展 + 待办 + 未读通知），用短 prompt 唤醒 Agent Runtime
（Cursor / Claude Code 等），Agent 读 Skill 后通过 `map --persona ...` 写回平台：

```bash
# 一键启动三个 persona 的 waker
./scripts/start-all-simple-wakers.sh

# 或者只启动单个 persona
./scripts/start-simple-waker.sh --persona host
```

- 状态文件：`.map/simple-waker-state-*.json`；日志：`.map/waker-logs/`
- 会话转录：`.map/runtime-waker-sessions/`（排查 Agent 被唤醒后做了什么）
- 停止：`pkill -f simple_waker` 或关闭对应终端
- 注意：改 waker 代码或 Skill 后需重启 waker

详见 [MAP-SIMPLE-WAKER.md](./MAP-SIMPLE-WAKER.md)。

---

## 常见问题

### Q: Docker 启动报错 `MAP_WEBHOOK_SECRET_ENCRYPTION_KEY is required`

`.env` 中的加密密钥为空。运行以下命令生成并填入：

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### Q: `map bootstrap` 报 `Admin token required`

新版 server（>=0.4）的 `map bootstrap` **无需 admin token**，会自动调用自助 `POST /api/v1/bootstrap` 端点完成 project + persona agent 创建。如果遇到此错误，说明你连接的是老版本 server（无自助端点），CLI 会自动回退到 admin token 路径。此时需要先注册首个 admin：

```bash
curl -X POST "http://localhost:18400/api/v1/agents?name=my-admin&role=admin"
export MAP_ADMIN_TOKEN=<返回的 api_token>
```

升级 server 到 0.4+ 即可免除此步骤。

### Q: 想换端口 / 端口仍被占用

API 默认端口为 `:18400`（Docker 侧见 `docker-compose.yml`；pip 侧见 `MAP_PORT`），
MCP 在本仓 override 中发布到 `:18081`。如需改端口：Docker 编辑
`docker-compose.override.yml` 的端口映射，pip 设 `MAP_PORT=<端口>` 启动
`map-server`，并保证 bootstrap 的 `--api-url` 与实际端口一致。

### Q: 重新 bootstrap 报 `agents.local.yaml already exists` / `.map/agents.local.yaml` 丢失、token 失效

首选**重签发**（旧 token 立即吊销，新 token 自动写回本地，server >= 0.11）：

```bash
map auth reissue --key my-project --name my-project-host
map --persona host persona whoami   # 验证
```

agent 名见 `.map/agents.yaml`（如 `multi-agent-platform-host`）。若确实想换一批
Agent，再删除旧文件重新 bootstrap（已注册的 Agent token 无法通过 bootstrap 恢复）：

```bash
rm .map/agents.local.yaml
map bootstrap --key my-project --name "My Project" --api-url http://localhost:18400 --force
```

---

## 下一步

- [CLI 完整命令参考](./CLI.md)
- [Python SDK 指南](./SDK.md)
- [MCP Server 指南](./MCP.md)
- [架构设计](./ARCHITECTURE.md)
- [协作 Skill 文档](../.cursor/skills/map-project-collab/SKILL.md)
