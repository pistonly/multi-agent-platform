---
title: "plan 文档不存 DB：分阶段 flag 门禁退役 plan_versions 全文写入"
topic: plan-db-content-retirement
mode: standard
executor_hint: participant
acceptance:
  - "A1-1 CLI 双写：`--plan-file`（内联）创建/revise 时 CLI 先物化 plan.md 到 FS（复用 materialize_experiment_plan 逻辑），DB 照旧写全文；flag off 行为与现状一致"
  - "A1-2 新 feature flag `plan_db_content_retired`（project 级，仿 fs_stop_duplicate_insert：合法触发人 host creator 或 admin，on 需非空 reason）"
  - "A1-3 flag on 门禁判据 = 请求体是否携带全文 content_md：内联分支拒绝（409/410 + 自助化报错文案，指向 --plan-file-path / materialize）；slim `--plan-file-path` 创建分支放行（其 content_md 为 None 时写 stub 的现状保留）"
  - "A1-4 revise 去重判据同步替换：early-return 的全文等值比对（plan_service.py:126-139）改为对 FS plan.md 内容哈希比较（或复用 materialize 防分歧校验）；flag on 后对同一 FS plan.md 重复 revise 不 bump 版本、不触发 _archive_prior_version_reviews"
  - "A2-1 `map experiment sync migrate` 增加 plan 类 kind，严格走三处联动 checklist（work_kinds registry / wake.md 标记块用 map work --kinds --kinds-format md 重新生成 / tests 一致性用例）"
  - "A2-2 存量迁移：批量把 DB 内联 plan 物化为 plan.md 后 content_md stub 化（仿 M57 `See file: <path>`）；迁移后 plan 域 `sync --check` 对账零 diff"
  - "A3-1 flag on 读路径 fail-closed：plan.md 缺失时报错并指向 materialize 修复"
  - "A3-2 文档与 Skill 分发面更新（commands.md / file-reference.md）；分发面无仓库专属引用（test_red_line_clause.py dogfood 反向守卫通过）"
evidence_keys:
  - "flag_plan_db_content_retired_on_reject_inline_create"
  - "flag_on_slim_create_still_ok"
  - "flag_on_duplicate_revise_no_bump_no_archive"
  - "sync_check_plan_domain_zero_diff_after_migration"
  - "pytest_full_green_plus_ruff_zero"
dependencies:
  - "话题 plan-db-content-retirement（Round 1 收敛结论 + A/B 两坑修正）"
  - "存量修复通道 map experiment plan materialize（1a7f158 已合入）"
  - "环境注记：cli/agent_client.py 有一处 waker effort 兜底补丁（MAP_RUNTIME_EFFORT，未随本实验范围），执行前由 host 先行窄 commit 保持 worktree 干净"
---

# 实验计划：plan 文档不存 DB（分阶段 flag 门禁）

## 背景

实验域 FS-first 迁移已完成读路径（index.md 契约 M1、读路径切 FS、`fs_stop_duplicate_insert`、`topic_db_read_retired`）与日志瘦身（M57 stub + file_path），但实验计划正文仍全文落 DB（`plan_versions.content_md`）。目标：plan 文档事实源收敛到 `map/experiments/<slug>/plan.md`，DB 只留 stub/引用，对齐话题域迁移模式。

## 实施项（按序推进，每项独立窄 commit）

### A1 阶段 0+1：CLI 双写 + flag 门禁（对应验收 A1-1 ~ A1-4）

- 阶段 0：`--plan-file` 内联创建/revise 时，CLI 侧先物化 plan.md（复用 `cli/experiment_fs.py` 的 `materialize_experiment_plan`：幂等 + frontmatter 校验 + 防分歧覆盖），DB 行为不变
- 阶段 1：`feature_flag_service.py` 新增 `plan_db_content_retired`（project 级）；`plan_service.py` revise 与 `project_service.py` create 两入口加门禁——判据是「请求体携带全文 content_md」，slim 分支放行（participant Round 1 A 点修正）
- revise 去重：全文等值 early-return 换成 FS plan.md 内容哈希判据（participant Round 1 B 点修正，防止 stub 化后重复 revise 误 bump + 误归档评审）
- 回归测试：flag off 全绿；flag on 拒内联/放 slim/重复 revise 不 bump 三个行为面各有用例

### A2 阶段 2：存量迁移（对应验收 A2-1 / A2-2）

- `map experiment sync migrate` 增 plan 类 kind（三处联动 checklist 强制）
- 批量物化 + stub 化存量内联 plan；`sync --check` plan 域零 diff 验证

### A3 阶段 3：读收口 + 文档（对应验收 A3-1 / A3-2）

- flag on 读路径 fail-closed（plan.md 缺失 → 报错指向 materialize）
- commands.md / file-reference.md 更新；分发面纪律守卫通过

## 验收标准（final，来自话题 Round 1 Summary）

1. flag off：全量回归通过，行为与现状一致
2. flag on：携带全文 content_md 的写请求被拒绝（报错自助化）；`--plan-file-path` slim 创建仍成功
3. flag on：对同一 FS plan.md 重复 revise 不产生新版本、不归档评审
4. 存量：materialize 批量迁移后 plan 域 `sync --check` 零 diff
5. 分发面：无仓库专属引用泄漏（dogfood 反向守卫通过）

## 执行约束

- 执行锁：acquire/release（TTL 1800s），每 wake 至少推进一个编号验收项并写 `experiment log`
- git：窄 commit（`map exp <short-id>: <summary>`），禁 `git add .`
- 完成后 standard 流程：complete（`--log-file-path map/experiments/plan-db-content-retirement/log.md`）→ result_review 等 reviewer 审批
