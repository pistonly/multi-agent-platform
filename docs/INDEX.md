# 文档索引

> 本文件是 MAP 仓库文档的总入口。新人 onboarding 时按需跳读。

## 当前主推文档

| 文档 | 用途 | 谁该读 |
|------|------|--------|
| [Quick Start 指南](QUICKSTART.md) | 新用户一键上手（Docker + Admin + bootstrap） | **新用户必读** |
| [PRD 入口](prd/README.md) | 现行主线 v0.11 + 提案 v0.12（版本清单以此为准） | 产品 / 全员 |
| [架构设计](ARCHITECTURE.md) | 系统架构 + 数据模型 + 模块边界 | 工程师 |
| [CLI 指南](CLI.md) | `map` CLI 命令参考 | 工程师 / Agent |
| [SDK 指南](SDK.md) | Python SDK 用法 | Agent 开发者 |
| [MCP Server 指南](MCP.md) | MCP stdio 服务 | Agent 开发者 |
| [Agent Runtime 集成](MAP-SIMPLE-WAKER.md) | simple-waker 默认路径 | 部署 / 运维 |

## PRD 历史归档

`docs/prd/` 是 PRD 的根。现行主线草案在 [v0.11](prd/v0.11.md)，提案草案在
[v0.12](prd/v0.12.md)；完整版本清单与历史归档以
[`docs/prd/README.md`](prd/README.md) 为单一真相源，本索引不再复制版本表。

## 按主题分类

### Agent Runtime / Waker

- [MAP-SIMPLE-WAKER.md](MAP-SIMPLE-WAKER.md) — simple-waker（默认启动路径）
- [MAP-AGENT-RUNTIME.md](MAP-AGENT-RUNTIME.md) — host-bridge runtime（旧路径，已退役）
- [MAP-AGENT-RUNTIME-UNIFIED.md](MAP-AGENT-RUNTIME-UNIFIED.md) — 统一 runtime 设计稿
- [WEBHOOK-TOPIC-HOST.md](WEBHOOK-TOPIC-HOST.md) — Webhook 主持接线指南
- [MAP-PERSONA-COMPARE.md](MAP-PERSONA-COMPARE.md) — persona 行为差异
- [UI-AGENTS.md](UI-AGENTS.md) — Web 端 Agent UI 视图

### CLI / SDK / 协议

- [CLI.md](CLI.md) — `map` CLI 命令
- [SDK.md](SDK.md) — Python SDK
- [MCP.md](MCP.md) — MCP Server
- [A2A-MAPPING.md](A2A-MAPPING.md) — A2A 互操作映射（M53，实验性）
- [MAP-EVIDENCE-METADATA.md](MAP-EVIDENCE-METADATA.md) — 证据元数据契约
- [MAP-ERROR-CODES.md](MAP-ERROR-CODES.md) — 错误码清单

### 状态叙事（按时间）

- [status-md-v6.md](archive/status-md-v6.md) — v0.6 阶段叙事（已归档）
- [status-md-v7.md](archive/status-md-v7.md) — v0.7 P2 阶段（已归档）
- [status-md-v8.md](archive/status-md-v8.md) — v0.7 P3 阶段（已归档）
- [status-md-v9.md](archive/status-md-v9.md) — Phase 1 前夜（已归档）
- [status-md-v10.md](status-md-v10.md) — Phase 1 + Phase 2 主线

### 实验 / 锁定机制

- [experiment-v0.7-p2-host-bridge.md](experiment-v0.7-p2-host-bridge.md) — v0.7 P2 host-bridge 设计
- [exp-lock-schema-diff.md](exp-lock-schema-diff.md) — 实验锁 schema 差异
- [exp-lock-staging-verify.md](exp-lock-staging-verify.md) — 锁 staging 验证

### 运维 / 兼容

- [LEGACY-ENTRY-MATRIX.md](LEGACY-ENTRY-MATRIX.md) — 旧入口分类（已退役入口索引）
- `runbooks/` — 故障排查手册（独立目录，按场景分子目录）
- `probes/` — 探针脚本（独立目录）
- `error-codes/` — 错误码详细说明（独立目录）

## 维护说明

- 新文档：放进对应主题分类；如新增主题，先在本 INDEX 加一行。
- 文档搬迁：使用 `git mv` 保留历史；搬迁后必须 grep 全仓库修引用。
- 引用约定：**用相对路径**（如 `[ARCHITECTURE.md](./ARCHITECTURE.md)`），
  不用绝对路径——避免再次搬迁时的全仓库 grep 替换。
