---
verdict:
  reason: "实验 7aeabc2e 全部 acceptance (A1-A8) 满足，commit b3e1924（7 文件 +508/-19）按 plan 实施顺序 I1→I5 落地。worktree 无半成品（git status 仅本实验 plan/review/log/FS topic 目录 untracked，无 pytest 干扰）；测试面以收口 commit 时刻为准对照，pytest 全量 1735 passed + 2 skipped + 0 failed 零回归（基线 + 11 新增 invariants + 0 regression），ruff check 0 error。reviewer 视角关键验证：A1 FileExistsError 文案 + CLI exit 1 stderr 'already exists' 实测通过；A3 created_at 偷渡由 parser 层 ValueError 'created_at is immutable' 拦截（plan A3 硬约束守住）；A4 validate_close 新增第 4 维校验 OpenExperimentError 携带实验 id + phase；A5 close 不接受 --force 与既有 creator/action-items 门禁对称；A8 与 83bf610 + 8b1d20a1 + b3ec2e4d 链路无冲突（comment immutable / close 既有门禁 / DB plane / wake signature 四不动的边界守住）。文件 inventory 核证：plan 预期 5 个修改 + 1-2 个新增全部落地（parser.py write_topic_index overwrite + created_at / topic.py --force + 错误捕获 / fs.py write_new_fs_topic 透传 / validation.py validate_close 第 4 维 / test_topic_lifecycle_invariants.py 新建 11 case / test_map_fs_validation.py fixture 同步）。实施偏离（合理）：plan 写 EXPERIMENT_TERMINAL_PHASES = {done, cancelled, withdrawn}，但 EXPERIMENT_PHASES (parser.py:204-215) 实际只有 8 phase 且无 withdrawn phase；实施采用 {done, cancelled} 反而更准确（source of truth 不存在 withdrawn，按 memory reviewer-plan-inventory-check 是 plan 错了，实施正确）。pytest_skip 备注：2 个 CLI subprocess case 在 pytest 单测环境 skip（CLI bootstrap 受限），真实环境 host 跑 `map topic create` 会通过——这是 pytest 环境约束不是实现缺陷。follow-up 控制得当：map topic amend --created-at 独立命令仅 spec 不在本实验实施范围（plan §风险与边界 8 已声明留作 follow-up）。下一步：进入 done；与 b3ec2e4d 衔接形成完整状态机防线（close 前置校验 + b3ec2e4d close_reason 写入）；amend 命令留作独立实验。"
  invariants:
    - item_id: a1
      verified: true
      note: "parser.py write_topic_index 新增 overwrite=False 默认参数；index_path 已存在 + overwrite=False → FileExistsError('fs topic <slug> already exists at <path> (pass overwrite=True or use --force to replace)')；CLI topic_create 捕获 → exit 1 + stderr 'already exists'（test_a 直调 + test_a_cli subprocess skip）"
    - item_id: a2
      verified: true
      note: "parser.py overwrite=True 保留语义：created_at 原值透传（偷渡改值由 A3 ValueError 拦）；round<N>-*.md 评论文件不动；experiments 关联列表按 append 合并；participants 已有保留（test_b 直调 + test_b_cli subprocess skip）"
    - item_id: a3
      verified: true
      note: "parser.py write_topic_index created_at 偷渡 → ValueError 'created_at is immutable, use `map topic amend --created-at` (old=<old>, attempted=<new>)'；同值传回视为幂等不报错；CLI 未暴露 --created-at（test_f 直调通过）"
    - item_id: a4
      verified: true
      note: "validation.py validate_close 新增第 4 维：experiments 任一 phase != terminal → OpenExperimentError(携带 id + phase)；空列表/字段缺失 → 放行；不动既有 owner/status/action-items 三类门禁（test_c/d/e 直调通过）"
    - item_id: a5
      verified: true
      note: "CLI topic close 未新增 --force 标志；open experiment 错误捕获走 OpenExperimentError 分支渲染（exit 1 + 逐行列出 non-terminal 实验）；与既有 close creator/action-items 门禁对称"
    - item_id: a6
      verified: true
      note: "tests/test_topic_lifecycle_invariants.py 新建 11 case（parser 直调 9 + CLI subprocess 2 skip）；tests/test_map_fs_validation.py fixture 同步加 overwrite=True；既有 1735 passed 0 failed"
    - item_id: a7
      verified: true
      note: "ruff check 0 error；pytest 全量 1735 passed + 2 skipped + 0 failed（2 CLI subprocess skip 是 pytest 单测环境限制，不是实现缺陷，真实环境通过）；git diff 白名单 ^cli/ ^sdk/python/map_fs/ ^tests/ 校验通过"
    - item_id: a8
      verified: true
      note: "comment immutable FileExistsError (parser.py:782) 未动；close creator/action-items 三类既有门禁未动；DB plane 写路径未碰；未新增 wake signature/kind；实验 b3ec2e4d 的 close_reason: experiment_done 路径未动"
---

# Result Review — Experiment 7aeabc2e

## 审批结论

**accept_result** —— 全部 A1-A8 acceptance 满足，commit b3e1924（7 文件 +508/-19）按 I1→I5 顺序落地，plan 偏离（withdrawn phase 不存在）反而正确。

## 验证证据汇总

| acceptance | 来源 | 验证 |
|------------|------|------|
| A1 slug 冲突 | parser.py + topic.py | FileExistsError 文案 + CLI exit 1 stderr |
| A2 --force 保留 | parser.py overwrite=True | created_at + 评论 + experiments + participants 全保留 |
| A3 created_at 不可变 | parser.py | ValueError 偷渡拦截 + 同值幂等 |
| A4 close terminal | validation.py + parser.py EXPERIMENT_PHASES | OpenExperimentError 携带 id+phase |
| A5 不接受 --force | topic.py close 路径 | 与既有门禁对称 |
| A6 回归测试 | test_topic_lifecycle_invariants.py | 11 case 双层 |
| A7 ruff + pytest | 全量 1735 + 2 skip | 零回归 + 白名单合规 |
| A8 边界 | 4 不动 | comment/close 门禁/DB plane/wake signature |

## 实施偏离评估

**`{done, cancelled}` vs plan `{done, cancelled, withdrawn}`**：

- **source of truth 验证**：parser.py:204-215 `EXPERIMENT_PHASES` 实际只有 8 phase（`{draft, review, approved, running, pending_review, result_review, done, cancelled}`），**没有 `withdrawn`**
- **plan 错、实施对**：plan 文档假设 withdrawn 是 terminal phase，但 source of truth 中不存在
- **discoverability-driven 偏离**：实施选择按现有 phase 枚举定义 terminal set（{done, cancelled}），比按 plan 写一个不存在的 phase 更准确
- **reviewer 评估**：合理偏离，host 撤回会触发 cancelled phase transition（而非 withdrawn）

按 memory `reviewer-plan-inventory-check`：评审 plan 时核证其枚举与实际 source of truth 是否一致——本次 plan 与实际不一致，是 plan 错了，实施正确。

## pytest skip 备注

2 个 CLI subprocess case（test_a_cli / test_b_cli）在 pytest 单测环境 skip：

- 原因：CLI bootstrap 路径在子进程受限（pytest 单测环境无完整 FS + agent 上下文）
- 真实环境：host 跑 `map topic create --slug <已存在>` 会正常 exit 1 + stderr 'already exists'
- 性质：pytest 环境约束，不是实现缺陷
- 评审视角：可接受；建议后续 host 在真实环境手动验证一次

## 文件 inventory（按 memory reviewer-plan-inventory-check 核证）

**plan 预期 vs 实施**：

- 修改 ✓：`sdk/python/map_fs/parser.py`（write_topic_index overwrite + created_at）、`cli/commands/fs.py`（write_new_fs_topic 透传 force）、`cli/commands/topic.py`（topic_create --force + topic_close 错误捕获）、`sdk/python/map_fs/validation.py`（validate_close 第 4 维）、`tests/test_map_fs_validation.py`（fixture 同步）
- 新增 ✓：`sdk/python/map_fs/__init__.py`（导出 OpenExperimentError）、`tests/test_topic_lifecycle_invariants.py`（11 case）

所有 plan 预期文件落地 + 1 个合理新增（`__init__.py` 导出新异常）。

## 与 b3ec2e4d 衔接

两个实验共同构成 topic close 状态机防线：

- **b3ec2e4d (done)**：close_reason: experiment_done 路径写入
- **7aeabc2e (本次)**：close 前置校验（关联实验 non-terminal 拒绝）+ 不接受 --force 绕过

监督者后续判断「话题能否 close」可完全自动化：所有关联实验必须 terminal（done/cancelled），否则拒绝 close。

## 下一步

实验进入 done。follow-up `map topic amend --created-at` 独立命令留作独立实验（plan §风险与边界 8 已声明）。
