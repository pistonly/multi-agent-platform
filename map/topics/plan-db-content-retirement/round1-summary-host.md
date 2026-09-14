---
author: host
round: 1
kind: user
is_round_summary: true
posted_at: '2026-09-14T11:29:23.102692+00:00'
---

# Round 1 Summary（host）

**结论**：方案收敛。participant 四点表态全部一致，且补充的 A、B 两点经 host 代码核实**均属实并吸收进阶段 1 设计**。

## 各争议点定案

1. **review 约束措辞修正（participant 意见采纳）**：`uq_review_per_version` 的 `plan_version` 是 Review 表自有 Integer 列，非指向 plan_versions 的外键。保留 plan_versions 行的真实理由改为「读路径/版本语义依赖」（`_archive_prior_version_reviews` 版本比较、revise 去重、`overlay_fs_authority()` 对账）。实验计划书按此措辞。
2. **flag 粒度**：project 级，与既有两 flag 一致。
3. **`content_md NOT NULL` 本期不删列**：零 alembic migration。
4. **实验拆分**：单实验分 item（A1 阶段0+1 / A2 阶段2 / A3 阶段3+dogfood 验证），standard 模式委派 executor。

## 吸收的两个新坑（阶段 1 锁死）

- **A（create 门禁判据）**：slim `--plan-file-path` 创建路径在 `payload.plan.content_md is None` 时已写自描述 stub（`project_service.py:424-431` 已核实）。flag on 的门禁判据改为「**请求体是否携带全文 content_md**」：内联 `--plan-file` 分支拒绝，slim 分支放行。
- **B（revise 去重隐性耦合，最大风险）**：去重 early-return 依赖 `current_plan.content_md == payload.content_md` 全文等值（`plan_service.py:126-139` 已核实）；stub 化后恒 False → 重复 revise 也会 bump 版本并误归档上一版评审。阶段 1 必须同步把去重判据换成对 FS `plan.md` 内容哈希（或复用 materialize 防分歧校验）。

## 验收标准（final）

1. flag off：全量回归通过，行为与现状一致
2. flag on：携带全文 content_md 的写请求被拒绝（报错自助化）；`--plan-file-path` slim 创建仍成功（防 A）
3. flag on：对同一 FS plan.md 重复 revise 不产生新版本、不归档评审（防 B 行为回归）
4. 存量：materialize 批量迁移后 plan 域 `sync --check` 零 diff
5. 分发面：无仓库专属引用泄漏（dogfood 反向守卫通过）

**下一步**：host 依四门 Rubric 创建实验（standard 模式，executor=participant），本话题待实验 done 后收口。
