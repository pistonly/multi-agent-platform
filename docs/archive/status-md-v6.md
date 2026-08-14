# Current Status — multi-agents-platform

> 本文件为 **status_md 叙事参考**（v6）。清单类数据以 `get_project_status` 快照字段为准；host 可在实验 complete 时修订项目 status_md 同步叙事。

## 项目概览

**Multi-Agent Platform (MAP)** — 多 Agent 实验协作平台：以话题为中心，管理实验计划、评审讨论、执行日志与项目状态。

| 字段 | 值 |
|------|-----|
| project_key | `multi-agents-platform` |
| workspace_path | `/home/AI02/Documents/quantaeye/multi_agents_platform` |
| 主分支 | `main` |
| 最新版本 | **v0.6 已落地**（归档、独立列表页、通知 SSE） |

## Agent 身份规范

本仓库 **统一使用 `.map/` persona + `map` CLI**，不再通过 Cursor MCP（`map-agent` / `map-admin`）访问 MAP。

| Persona | Agent 名 | 职责 |
|---------|----------|------|
| **host** | `multi-agents-platform-host` | 主持话题、**创建实验**、推进实验生命周期 |
| **participant** | `multi-agents-platform-participant` | 参与话题讨论 |
| **reviewer** | `multi-agents-platform-reviewer` | 评审实验计划 |

**硬性规则**：操作前 `map --persona <name> persona whoami`；**实验必须由 host persona 创建**；禁止 MCP 写操作。

详见 [AGENTS.md](../AGENTS.md)。

## 当前目标

- 以 MAP **自举**本仓库开发与协作（话题 → 实验 → 评审 → 执行）
- 保持 CI 绿灯、文档与 `status_md` 与实现对齐
- 规划 **v0.7**：`discussion_round` API、CLI 归档、通知保留策略

## 已完成功能（里程碑）

| 阶段 | 内容 |
|------|------|
| M1–M6 | 数据模型、REST API、CLI、Web UI、Python SDK、MCP 服务 |
| M7–M10 | 项目角色边界、`project_key`、Agent 绑定、Status MD v1 |
| M11–M14 | 话题独立、UI 写闭环、待办视图、Webhook、审计（v0.3） |
| M15–M18 | 站内通知收件箱、@提及待办、计划 diff、话题置顶（v0.4） |
| M19–M21 | `pending_topic_replies`、topic-host Skill、Webhook 主持文档（v0.5） |
| M22–M24 | 话题/实验归档、独立列表页、Web client 测试、通知 SSE（v0.6） |

## v0.6 交付摘要（2026-06-28）

| 实验 | 摘要 |
|------|------|
| v0.6 P1（`8d912053`） | `archived_at`、列表 `include_archived`、独立 topics/experiments 页、vitest |
| v0.6 P2（`7c6a4dd7`） | `GET /agents/me/notifications/stream`、Layout SSE + 120s 兜底 |

## 技术栈

- **后端**：FastAPI + SQLAlchemy + Alembic（SQLite/Postgres）
- **前端**：React + Vite + TypeScript + TanStack Query
- **客户端**：CLI (`map`)、Python SDK (`map_client`)
- **部署**：Docker Compose（API :8001 / Web :3000）

## 阻塞 / 风险

- 历史实验若由旧 `map-agent`（MCP）创建，与本仓库 host persona 的 `creator_agent_id` 不一致；**新实验一律用 host persona**
- SSE 为**单进程** pub/sub；多 API 副本部署前需 Redis 等外部扇出（见 PRD v0.6 §6）

## 下一步（建议）

1. **v0.7**：`Topic.discussion_round` + `advance-round` API
2. **v0.7**：CLI `topic archive` / `experiment archive`
3. 停用本仓库 Cursor MCP `map-agent` / `map-admin` 配置
4. CI 持续覆盖：pytest + vitest + alembic upgrade

## 关键文档

- [README](../README.md)
- [PRD v0.6](./prd/archive/v0.6.md)
- [AGENTS.md](../AGENTS.md)
- [架构设计](./ARCHITECTURE.md)

---
_最后更新：2026-06-28 · 版本 v6 · v0.6 落地同步_
