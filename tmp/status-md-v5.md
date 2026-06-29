# Current Status — multi-agents-platform

## 项目概览

**Multi-Agent Platform (MAP)** — 多 Agent 实验协作平台：以话题为中心，管理实验计划、评审讨论、执行日志与项目状态。

| 字段 | 值 |
|------|-----|
| project_key | `multi-agents-platform` |
| workspace_path | `/home/AI02/Documents/quantaeye/multi_agents_platform` |
| 主分支 | `main` |
| 最新版本 | **v0.5 已落地**；v0.5.1 增量（通知定向、topic_id 软校验）已落地 |

## Agent 身份规范（P0，2026-06-27）

本仓库 **统一使用 `.map/` persona + `map` CLI**，不再通过 Cursor MCP（`map-agent` / `map-admin`）访问 MAP。

| Persona | Agent 名 | 职责 |
|---------|----------|------|
| **host** | `multi-agents-platform-host` | 主持话题、**创建实验**、推进实验生命周期 |
| **participant** | `multi-agents-platform-participant` | 参与话题讨论 |
| **reviewer** | `multi-agents-platform-reviewer` | 评审实验计划 |

**硬性规则**

1. 操作前：`map --persona <name> persona whoami`
2. **实验必须由 host persona 创建**，否则后续 submit/approve/start/complete 会因 `creator_agent_id` 不匹配而 403
3. 禁止手写 HTTP；禁止依赖 MCP 写操作（MCP 接入计划停用）

详见 [AGENTS.md](../AGENTS.md) 与 [.cursor/skills/map-project-collab/SKILL.md](../.cursor/skills/map-project-collab/SKILL.md)。

## 当前目标

- 以 MAP **自举**本仓库开发与协作（话题 → 实验 → 评审 → 执行）
- 保持 CI 绿灯、文档与 `status_md` 同步
- 规划 **v0.6**：列表归档、独立列表页、Web 前端测试、SSE 推送

## 已完成功能（里程碑）

| 阶段 | 内容 |
|------|------|
| M1–M6 | 数据模型、REST API、CLI、Web UI、Python SDK、MCP 服务 |
| M7–M10 | 项目角色边界、`project_key`、Agent 绑定、Status MD v1 |
| M11–M14 | 话题独立、UI 写闭环、待办视图、Webhook、审计（v0.3） |
| M15–M18 | 站内通知收件箱、@提及待办、计划 diff、话题置顶（v0.4） |
| M19–M21 | `pending_topic_replies`、topic-host Skill、Webhook 主持文档（v0.5） |
| P0–P3 | Bundle API、Token 前缀索引、Webhook 异步、API 模块化、CI |

## 近期已完成实验（2026-06-27）

| 实验 | 摘要 |
|------|------|
| Web UI 列表分页 + 筛选（P0） | ProjectStatusPanel 话题/实验列表分页、筛选、搜索 |
| P1 主持工作流 v0.5.1 | 通知定向 creator、topic_id 软校验 |
| P0 Agent 体验 | Web `pending_topic_replies`、CLI bootstrap 提示 |
| v0.5 主持工作流 | `pending_topic_replies` + topic-host Skill |

当前无 active 实验、无 open 话题（`experiment_counts`: done 10, cancelled 3）。

## 技术栈

- **后端**：FastAPI + SQLAlchemy + Alembic（SQLite/Postgres）
- **前端**：React + Vite + TypeScript
- **客户端**：CLI (`map`)、Python SDK (`map_client`)
- **部署**：Docker Compose（API :8001 / Web :3000）

## 开发环境

```bash
pip install -e ".[dev]"
alembic upgrade head
map-server                    # API
cd web && npm run dev         # Web UI
docker compose up --build     # 全栈

# 本仓库协作（推荐）
map bootstrap --key multi-agents-platform --name "Multi Agents Platform" --api-url http://localhost:8001
map --persona host persona whoami
map --persona host todos
```

## 阻塞 / 风险

- 历史实验由旧 `map-agent`（MCP）创建，与本仓库 host persona 不一致；**新实验一律用 host persona**
- `status_md` 曾长期滞后于实现，已在本版本修订；后续实验 complete 时同步更新叙事层

## 下一步（建议）

1. **v0.6 P1**：话题/实验归档 + 独立列表页（延续分页话题剩余诉求）
2. **v0.6 P1**：Web `client.ts` 单元测试 + 分页 smoke 测试
3. **v0.6 P2**：SSE/WebSocket 实时通知（替代 30s 轮询）
4. 停用 Cursor MCP `map-agent` / `map-admin` 配置

## 关键文档

- [README](../README.md)
- [AGENTS.md](../AGENTS.md)
- [PRD v0.5](docs/PRD-v0.5.md)
- [架构设计](docs/ARCHITECTURE.md)
- [SDK 指南](docs/SDK.md)

## Agent 协作提示

典型工作流（**仅用 CLI + `.map/` persona**）：

```
1. map --persona host persona whoami
2. map --persona host status          # 快照 + status_md
3. map --persona host todos
4. 轻讨论：map --persona host topic create / participant topic comment
5. 正式实验：map --persona host experiment create → reviewer review → host approve → start → complete
```

> 实验/话题清单以 `get_project_status` 快照字段为准，勿从 status_md 解析。

---
_最后更新：2026-06-27 · 版本 v5 · Agent 身份规范 + v0.5 落地同步_
