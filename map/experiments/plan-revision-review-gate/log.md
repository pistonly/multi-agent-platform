# 实验日志：plan-revision-review-gate (bd9b21f6)

## 2026-08-24 15:16 · approve 完成，执行安排

- plan v2 评审通过（review 92be4276，无 open unreasonable 项）→ host approve，phase=approved
- start + I1 执行留待下轮唤醒：执行锁单 running 约束（与本批 124e9a00 同批 approved，逐个 start 执行）；approved 为稳定相位无空转
- v2 修订内容见 plan change_note：A7 重评解除状态机定稿 + A4 单一拒绝处置

## 2026-08-25 · I1-I5 完成（一次性落地，commit 5b4cfe6 / 4cf4181）

- **I1 标记与迁移（A1/A3）**：`plan revise --breaking-audit` 旗标 + change_note 首行 `breaking:` 前缀 → running→pending_review；state_machine 登记 running↔pending_review 迁移；非 breaking revise 保持 running 不动相位（A3）
- **I2 complete 拦截与解除（A2/A7）**：pending_review 期间 complete 一律拒（报错 actionable：待重评 plan 版本 + 剩余 open unreasonable 阻塞数）；`review add` 无 open unreasonable 项 → 同事务自动迁回 running 解除（判定与 count_open_unreasonable 同口径）；仍含则持续拒。create_review 相位许可扩到 (review, pending_review)
- **I3 误标容忍（A6）**：误打 breaking → 回 pending_review 一次，重评确认非 breaking 即解除；一次纠正不惩罚、无红旗（负向单测覆盖）
- **I4 change_note 入口校验（A4）+ complete 版本核对红旗（A5）**：breaking revise 缺/过短 change_note 在 revise 入口 422 拒绝（单一处置，无警告分支）；complete 时当前 plan 版本无评审覆盖且存在更旧版本评审 → 完成日志落「breaking 漏标红旗」供 reviewer result_review 核对（非阻塞，A3 合法 non-breaking 修订不被打断）
- **I5 单测与实测收尾**：tests/test_breaking_audit_gate.py 9 项验收单测全绿（A1/A2/A3/A4/A5±/A6/A7 三分支，db_session fixture，savepoint 回滚隔离）+ 相关服务套件 80 绿 + ruff check 通过
- A5 新增 has_review_on_current_plan_version helper（非归档评审覆盖当前版本判定）；A7 解除判定与红旗判定复用同一 count/查询口径，重评完整链路（打回→重评→解除→complete）无红旗
- 已知无关红：tests/cli/test_compat.py topic 快照缺 `action-item`（6889869 已提交代码、快照未跟）为预存测试债，属 50cddb7e 范围不越界修
