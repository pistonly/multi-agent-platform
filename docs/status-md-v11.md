# Current Status — multi-agents-platform

> 本文件为 **status_md 叙事参考**（v11）。清单类数据以 `get_project_status` 快照字段为准，勿在本 MD 维护话题/实验清单。

## 项目概览

**Multi-Agent Platform (MAP)** — 多 Agent 实验协作平台：以话题为中心，管理实验计划、评审讨论、执行日志与项目状态。

| 字段 | 值 |
|------|-----|
| project_key | `multi-agents-platform`（本仓库 bootstrap；模板示例常写作 `multi-agent-platform`） |
| workspace_path | `/home/AI02/Documents/quantaeye/multi_agents_platform` |
| 最新版本 | **v0.15 收口**（M62 废弃 platform feedback；persona 后缀解析；看板话题写入改为 CLI 指引） |

## Agent 身份与协作路径

统一 `.map/` persona + `map` CLI + simple-waker。Agent 名为 **`{project_key}-{persona}`**（本仓库即 `multi-agents-platform-host` / `-participant` / `-reviewer`）。平台按名称**尾段**解析 persona，不依赖某一套 long name 字面量。

| Persona | Agent 名（本仓库） | 职责 |
|---------|-------------------|------|
| **host** | `multi-agents-platform-host` | 主持话题、创建实验、推进生命周期 |
| **participant** | `multi-agents-platform-participant` | 参与话题讨论 |
| **reviewer** | `multi-agents-platform-reviewer` | 评审实验计划与结果 |

标准路径：`./scripts/start-all-simple-wakers.sh` → Agent 读 Skill → `map --persona <name>` CLI。`cli/host_worker` bridge 与 legacy `runtime-waker` 启动路径已停用。

## 里程碑进展（截至 2026-08-25）

| 版本 | 里程碑 | 状态 |
|------|--------|------|
| v0.10–v0.11 | waker 统一、FS source-of-truth 瘦身（MAP 平台瘦身为交互索引） | ✅ 已落地 |
| v0.12 | **M54** machine-readable-cli / **M55** actionable-error-envelope / **M56** topic-id-routing | ✅ 已落地 |
| v0.13 | **M57** 实验日志瘦身对齐 / **M58** DB 话题写路径退役 / **M59** 性能基线与工程卫生 | ✅ 已落地 |
| v0.14 | **M60** `map fs archive` / **M61** `map fs archive-index --rebuild` | ✅ 已落地 |
| v0.15 | **M62** 废弃 platform feedback；实验 FS 化 **M1**（`map/experiments/<slug>/index.md` 契约） | ✅ 已落地 |

**话题面**：无 open 话题、无活跃实验。closed 话题走 `map --persona host fs archive --topic <slug>` 归档；实验写仍走 API，话题写入走本地 CLI（看板提供可复制命令）。

## 阻塞 / 风险

- 无阻塞项。遗留观察项：SSE 单进程 pub/sub（多副本需外部扇出，暂不引入）。
- `map experiment cancel` CLI 已存在（M55 评审时曾记为缺失，现已过时）。

## 下一步（建议）

1. 实验生命周期 FS 化 **M2**：`map experiment sync --check` 对账零 diff 后再停 INSERT（尚未立项）
2. `map topic` 写命令从 help 消失或变成 `map fs` 薄别名（双轨收口）
3. CI 持续：pytest + ruff + alembic upgrade

## 关键文档

- [README](../README.md)
- [PRD v0.15](./prd/v0.15.md)（入口：[prd/README.md](./prd/README.md)；历史见同目录归档表）
- [MAP-SIMPLE-WAKER.md](./MAP-SIMPLE-WAKER.md)
- [AGENTS.md](../AGENTS.md)
