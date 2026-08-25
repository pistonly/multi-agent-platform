# 多 Agent 平台 — 产品需求文档（PRD）

> 这是 MAP（Multi-Agent Platform）的 PRD 入口。**v0.11–v0.15 均已落地**；
> 现行叙事以 **[v0.15](./v0.15.md)** 收口。下一刀产品工作是实验生命周期
> FS 化 M2（`map experiment sync --check` 对账后再停 INSERT），尚未单独立项。
> 更早版本在下方归档表与 [`archive/`](./archive/)。

## 现行草案

- **[v0.15 — 废弃 platform feedback](./v0.15.md)**：已落地（M62）。看板话题写入改为 CLI 指引、persona 按 `{project_key}-{persona}` 后缀解析；实验 FS 化 M1（`index.md` 契约）已 done。

## 历史归档（按时间倒序）

| 版本 | 主题 | 链接 |
|------|------|------|
| v0.14 | FS 归档命令与自动索引 | [v0.14.md](./v0.14.md) |
| v0.13 | FS 事实源单轨化收尾 | [v0.13.md](./v0.13.md) |
| v0.12 | Agent 人机工程（M54–M56） | [v0.12.md](./v0.12.md) |
| v0.11 | Agent 体验与生态互操作（M50–M53） | [v0.11.md](./v0.11.md) |
| v0.10 | waker 统一与 FS 瘦身 | [v0.10.md](./v0.10.md) |
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
  `docs/prd/vN.md` 并更新本 README 的「现行草案」段。现行段必须包含
  `docs/prd/v*.md` 中版本号最大的那一份（测试锁定）。
- **不要**在仓库根 `docs/` 下放 PRD 文件；统一在本目录或
  `archive/` 下。
- 跨文件引用：用相对路径，例如 `[archive/v0.6.md](./archive/v0.6.md)`，
  不要写绝对路径（避免后续再次搬迁时全仓库 grep 替换）。
