# Current Status — multi-agent-platform

> 本文件为 **status_md 叙事参考**（v11）。清单类数据以 `get_project_status` 快照字段为准，勿在本 MD 维护话题/实验清单。

## 项目概览

**Multi-Agent Platform (MAP)** — 多 Agent 实验协作平台：以话题为中心，管理实验计划、评审讨论、执行日志与项目状态。

| 字段 | 值 |
|------|-----|
| project_key | `multi-agent-platform` |
| workspace_path | `/Volumes/disk_2/Users/liuyang/Documents/quantaeye/multi-agent-platform` |
| 最新版本 | **v0.13 FS 单轨化收尾**（实验日志瘦身对齐、DB 话题写路径直接退役、性能基线与工程卫生） |

## Agent 身份与协作路径

统一 `.map/` persona + `map` CLI + simple-waker。

| Persona | Agent 名 | 职责 |
|---------|----------|------|
| **host** | `multi-agent-platform-host` | 主持话题、创建实验、推进生命周期 |
| **participant** | `multi-agent-platform-participant` | 参与话题讨论 |
| **reviewer** | `multi-agent-platform-reviewer` | 评审实验计划与结果 |

标准路径：`./scripts/start-all-simple-wakers.sh` → Agent 读 Skill → `map --persona <name>` CLI。`cli/host_worker` bridge 与 legacy `runtime-waker` 启动路径已停用。

## 里程碑进展（截至 2026-08-22）

| 版本 | 里程碑 | 状态 |
|------|--------|------|
| v0.10–v0.11 | waker 统一、FS source-of-truth 瘦身（MAP 平台瘦身为交互索引） | ✅ 已落地 |
| v0.12 | **M54** machine-readable-cli（JSON 输出契约）/ **M55** actionable-error-envelope（422 统一 handler + frontmatter 模板 hint + E8 配对门禁 + Skill 日志纪律）/ **M56** topic-id-routing | ✅ 三实验均 accept-result（评审独立复测通过） |
| v0.13 | **M57** 实验日志瘦身对齐（复用 M55 双形态门禁）/ **M58** DB 话题写路径直接退役（Skill FS 化硬前置）/ **M59** 性能基线与工程卫生（scan_plane p50 7.6ms，距触发线 10 倍余量） | ✅ 三实验均 done |

**话题面**：v0.12 / v0.13 提案评审话题已收口（close note 沉淀 decision/rationale/action_items）；冒烟遗留话题已清理；当前无 open 话题、无活跃实验。

## 阻塞 / 风险

- 无阻塞项。遗留观察项：SSE 单进程 pub/sub（多副本需外部扇出，暂不引入）；M55 评审非阻塞建议——`map experiment cancel` CLI 封装缺失（端点 + SDK 已可用）。

## 下一步（建议）

1. host 评估 `map experiment cancel` CLI 封装是否纳入下一版本 PRD（v0.12 收口 action_item）
2. 发起 v0.14 PRD 讨论（候选输入：experiment log `--log-file-path` 后续需求、FS 话题 uuid5 可被 `experiment.topic_id` 引用的移交接班项）
3. CI 持续：pytest + ruff + alembic upgrade

## 关键文档

- [README](../README.md)
- [PRD v0.13](./prd/v0.13.md)（历史：[v0.12](./prd/v0.12.md) 及 [archive](./prd/archive/)）
- [MAP-SIMPLE-WAKER.md](./MAP-SIMPLE-WAKER.md)
- [AGENTS.md](../AGENTS.md)
