# breaking revise 门禁（bd9b21f6）— 结果审批（accept）

## 结论

**通过**。代码改动全部收口进 HEAD（5b4cfe6 / 4cf4181：state_machine / enums / phase_service / plan_service / review_service / test_breaking_audit_gate），plan v2 验收 A1-A7 逐条核证成立，单测 9 passed + ruff 全绿。

## 验收逐条核验（对照 git HEAD）

| 验收 | 判定 | 代码证据 |
|------|------|------|
| A1 标记与迁移 | ✅ | `server/domain/state_machine.py:33-36` running 增 `pending_review` 迁移；`plan_service.py:189` `--breaking-audit` / change_note 首行 `breaking:` 解析 → running→pending_review；pytest 用例 `test_breaking_revise_moves_running_to_pending_review` |
| A2 complete 真拦截 | ✅ | `phase_service.py:330-339` pending_review 期间 complete 一律拒，报错 actionable（"breaking revise 待重评: plan vN … open unreasonable items: M"）；用例 `test_complete_blocked_in_pending_review` |
| A3 非 breaking 留 running | ✅ | `plan_service.py` 仅 breaking 分支改 phase；非 breaking 走原路径不触迁移表；用例 `test_non_breaking_revise_stays_running` |
| A4 change_note 单一拒绝 | ✅ | `plan_service.py:197-201` breaking revise 缺/过短（<10 字符）change_note → `StateTransitionError`（422）拒绝，「改了什么/为什么」两要素提示；用例 `test_breaking_revise_without_change_note_refused` |
| A5 版本核对红旗 | ✅ | `phase_service.py:347-361` `has_review_on_current_plan_version` 判定 + 完成日志落「breaking 漏标红旗」（非阻塞、A3 合法 non-breaking 不被打断）；正负两用例 `test_a5_red_flag_when_revised_without_reexperience` / `test_a5_no_red_flag_after_review_covers_current_version` |
| A6 误标容忍 | ✅ | 误打 breaking → 回 pending_review 一次、重评确认非 breaking 解除、无红旗不惩罚；用例 `test_a6_misfire_breaking_corrected_once_no_penalty` |
| A7 重评解除状态机 | ✅ | `review_service.py:433-439` `phase==pending_review` 且 open unreasonable=0 → 自动迁回 running（同事务）；仍含则保持并持续拒；两用例 `test_re_review_without_unreasonable_releases` / `test_re_review_with_unreasonable_stays_blocked` |
| 状态机归档兼容 | ✅ | migrate 表仅增预期边（running→pending_review、pending_review→running/cancelled），done 仍为终态集；review_count 归档语义未破坏（e8f1b8c1 兼容） |
| 测试面 | ✅ | **reviewer 实测** `pytest tests/test_breaking_audit_gate.py` → 9 passed；`ruff check` 六受影响文件 All checks passed |
| 自举约束 | ✅ | 本实验执行期自身走 --breaking-audit 路径（执行 raise 未触发）——门禁落位即验收自证；A2 造实验实测记录于日志 |

## 独立复测确认

- 上次瞬态排除：工作区 `enums.py`/`state_machine.py` 的 running 半成品（_OWNERS 未跟上导致 import 断言炸）已在收口时消解——本次 9 passed 为实测结果
- 工作区其余 M/D（AGENTS.md / scripts/start-all-wakers.sh 等）属 waker 启动线（CLAUDE.md 已改指 `start-all-simple-wakers.sh`，file://去除 facade），与本实验零相关，不影响验收

## 遗留（非阻塞）

- 日志 I5 声明「相关服务套件 80 绿」未附具体套件清单——本批准以 target 测试 9 passed + ruff 为准；全量回归快照债（tests/cli/test_compat.py topic 快照缺 action-item）属 50cddb7e 范围，不在此实验清缴
