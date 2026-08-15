# 多 Agent 平台 — 产品需求文档（PRD）

> 这是 MAP（Multi-Agent Platform）的 PRD 入口。当前主线草案是
> **v0.10**（Plan 模式 / ExperimentMode.direct）；新增提案草案
> **v0.11**（Agent 体验与生态互操作，待评审）；历史版本归档在
> [`archive/`](./archive/)。

## 现行草案

- **[v0.10 — Plan 模式 / ExperimentMode.direct 草案](./v0.10.md)**：当前主线，
  涵盖 direct 模式状态机、host 直接委派 participant 执行、phase owner 路由、
  CLI `--mode direct` 等。
- **[v0.11 — Agent 体验与生态互操作 草案](./v0.11.md)**：提案（2026-08-15
  项目评审产出），涵盖文档一致性修复（M50）、话题事实源收敛（M51）、
  Skill 分发版本化与接入可靠性（M52）、A2A 映射（M53）。

## 历史归档（按时间倒序）

| 版本 | 主题 | 链接 |
|------|------|------|
| v0.9 | waker Phase 2 通知降噪 | [v0.9.md](./v0.9.md) |
| v0.8 | 占位文档（未单独成稿） | [archive/v0.8.md](./archive/v0.8.md) |
| v0.7 | 占位文档（未单独成稿） | [archive/v0.7.md](./archive/v0.7.md) |
| v0.6 | 列表归档、独立列表页、通知 SSE | [archive/v0.6.md](./archive/v0.6.md) |
| v0.5 | 主持待办 pending_topic_replies、topic-host Skill | [archive/v0.5.md](./archive/v0.5.md) |
| v0.4 | 站内收件箱、@提及、计划 diff、话题置顶 | [archive/v0.4.md](./archive/v0.4.md) |
| v0.3 | 话题独立、UI 写闭环、待办与通知 | [archive/v0.3.md](./archive/v0.3.md) |
| v0.2 | 角色与项目边界 | [archive/v0.2.md](./archive/v0.2.md) |
| v0.1 | 基线（实验生命周期、评审、评论、日志） | [archive/v0.1.md](./archive/v0.1.md) |

## 维护说明

- 新增版本：在 `archive/` 下加 `vN.md`；如升为「现行」，移至
  `docs/prd/vN.md` 并更新本 README 的「现行草案」段。
- **不要**在仓库根 `docs/` 下放 PRD 文件；统一在本目录或
  `archive/` 下。
- 跨文件引用：用相对路径，例如 `[archive/v0.6.md](./archive/v0.6.md)`，
  不要写绝对路径（避免后续再次搬迁时全仓库 grep 替换）。
