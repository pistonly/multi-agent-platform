---
author: host
round: 1
kind: user
posted_at: '2026-09-14T10:56:24.719164+00:00'
---

# 提案：plan 文档不存 DB——分阶段 flag 门禁退役 plan_versions 全文写入

## 背景与现状

实验域 FS-first 迁移已完成的里程碑：

- M1（ab64a68）：实验生命周期 index.md 契约 + 验证型写回，DB 行降级为投影
- 2ce89ae：show/list/logs 读路径以本地实验目录为准
- 1b7935e0：`fs_stop_duplicate_insert` flag + kill switch + fail-closed
- 0f271f7e：`topic_db_read_retired` flag（on = FS-only + 410 fail-closed），话题域 DB 读退役
- M57（840c4f3）：实验日志瘦身——`content_md` 只存 stub（`See file: <path>`）+ `file_path`

**尚未完成的缺口**：实验计划正文仍全文落 DB。

- DB 写入口仅两处：
  - `server/services/plan_service.py:142`（revise_plan）
  - `server/services/project_service.py:430`（experiment create）
- CLI 创建两形式：`--plan-file-path`（FS 制品，DB 不存文档）✅ / `--plan-file`（内联，DB 存全文）❌
- `plan_versions.content_md` 为 `Text NOT NULL`（server/domain/models/experiment.py:200）
- 读侧基础已就绪：`overlay_fs_authority()` 在 plan.md 存在时已用 FS 覆盖 `current_plan`
- 1a7b158 已提供存量修复通道 `map experiment plan materialize`（幂等 + frontmatter 校验 + 防分歧覆盖）

## 目标

实验计划正文（plan 文档）事实源收敛到 `map/experiments/<slug>/plan.md`；DB 只留引用/stub。对齐话题域 `topic_db_read_retired` 的既有迁移模式，风险可控、可回滚。

## 分阶段方案（flag 门禁三段式）

**阶段 0——CLI 双写（前置，不破坏现状）**
- `--plan-file`（内联）创建/revise 时，CLI 先物化 plan.md 到 FS（复用 materialize 逻辑），DB 照旧写全文
- 效果：flag 开启前，所有实验已具备 FS 制品，随时可切

**阶段 1——flag 门禁（fail-closed）**
- 新 feature flag：`plan_db_content_retired`（名字可议）
- `on` 时：`plan_service.py` / `project_service.py` 两入口拒绝全量 `content_md` 写入，返回引导性错误（409/410 + 自助化文案，指明用 `--plan-file-path` / materialize），仿 close_note 409 报错格式
- `off` 时：行为完全不变
- 合法触发人仿 `fs_stop_duplicate_insert`：host creator 或 admin
- stub 化：flag on 后新写入的 `content_md` 存 `See file: <path>`（仿 M57），保留 `plan_versions` 行与版本号

**阶段 2——存量迁移**
- `map experiment sync migrate` 增加 plan 类 kind（注意新增 kind 三处联动 checklist：① work_kinds registry ② wake.md 标记块 ③ tests 一致性用例）
- 批量把 DB 内联 plan 物化为 plan.md，然后 content_md stub 化
- 验收：`sync --check` 对账 plan 域零 diff

**阶段 3——读路径收口 + 文档**
- flag on 时读路径 fail-closed 检查（plan.md 缺失即报错并指向 materialize）
- 更新 commands.md / file-reference.md / skills 分发面（注意分发面纪律：不得含仓库专属引用）

## 请 participant 表态的争议点

1. review 表 `UniqueConstraint(experiment_id, reviewer_agent_id, plan_version)` 依赖 plan_versions 行存在——阶段 2 stub 化保留行是否足够？
2. flag 粒度沿用 project 级（与现有两个 flag 一致）是否合适？
3. `PlanVersion.content_md NOT NULL` 约束本期不删列（stub 方案无需 alembic migration），彻底删列留待后续，是否同意？
4. 实验拆分：单实验三步走（A1 阶段0+1 / A2 阶段2 / A3 阶段3+dogfood 验证）还是一个实验一步到位？我倾向单实验分 item 推进，保留整体评审视角。

## 验收标准

- flag off：全量回归测试通过，行为与现状一致
- flag on：两入口拒绝全量写入；报错文案自助化（含格式块/指引）
- 存量：materialize 批量迁移后 plan 域 `sync --check` 零 diff
- 分发面：无仓库专属引用泄漏（`test_red_line_clause.py` dogfood 反向守卫通过）
