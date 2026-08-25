# 测试债清偿（50cddb7e）— 结果审批（reject，窄返工）

## 结论

**驳回（窄返工）**。A1 清零、A3 evidence 机器校验、A7 closed 校验等**均核证成立**（含 reviewer 独立复跑）；唯 plan v3 测试面写死的「`ruff check` 全绿（v3 基线 4 预存红一并清零）」**未落实**——实测整仓 `ruff check .` 仍有 4 个基线红原样存在，执行日志「整仓全绿（清零后为 0）」与事实不符。返工面窄：只清 4 个 ruff 基线红并整仓复跑证明绿色，其余验收不返工。

## 已核证通过项（不要求返工）

| 验收 | 判定 | 证据（reviewer 独立实测） |
|------|------|------|
| A1 清零 | ✅ | **复跑 I0 机器清单 15 文件集**（i0-failed32-list 全部涉及文件）：`pytest` → **187 passed / 0 failed**；I0 原始清单与 32 红落盘完整（`i0-failed32-list-20260825.txt` / `i0-pytest-baseline-raw-20260825.txt`），与手工 25 的差 7 = v2「机器清单为准、多退少补」如实执行 |
| A2 防新漂移 | ✅ | commit 链逐族标题注明（I1 CLI envelope/format、I2 map_sdk、I3 health/agent/envelope/review_list/action-item），`log-execution.md` 逐族记录「修断言 / 修源码」归因 |
| A3 evidence 机器校验 | ✅ | `server/services/evidence_service.py:121` `validate_pytest_summary`；failed>0 无豁免拒 complete、total 对 `MAP_CI_TEST_TOTAL` 不符软 warning、非 dict legacy 不拒；`ExperimentComplete.known_failures` + CLI `--known-failures` 透传；**实测** `test_pytest_summary_gate.py` + `test_evidence_service.py` 绿 |
| A4 accept-result 可视化 | ✅ | accept-result 路径在携带 pytest_summary 的 log 上复校，校验段入 completion log；知识随 A3 测试覆盖 |
| A5 fast-gate 存量打标 | ✅ | 一致性实证 359 打标 == 359 deselected；sleep/subprocess 候选逐一核非真慢；无静默残留 |
| A6 probe 分口径 | ✅ | `test_zz_fastgate_probe.py` 源文件已删（留 .pyc），独立附注不混入 26 红口径 |
| A7 closed 校验 | ✅ | `cli/commands/topic.py:1400-1410` closed 话题 `action-item add` Exit 1 硬拒绝（closed=零尾款 invariant 注释明确）；**实测** `test_fs_action_items.py` 绿（含 open 回归护栏） |
| 新增门禁测试 | ✅ | **实测 48 passed**（test_pytest_summary_gate + test_evidence_service + test_fs_action_items） |

## 驳回理由（返工项）

**R1：`ruff check 全绿` 未达成（plan v3 测试面写死项，尝试即验收口径）**。实测整仓 `ruff check .` = **4 errors**，全部为 v3 基线红原样：
- `cli/session_wake_log.py:104` UP038（`isinstance(x, (X, Y))` → 应 `X | Y`），文件最后修改为 `2da53f3`（早于本实验，未被任何 50cddb7e commit 触碰）
- `tests/test_049_project_fs_content_config.py:3` / `test_review_archived_metadata.py:23` / `test_review_item_state_closed_migration.py:19` I001×3（可 `ruff --fix` 自动修），文件均早于本实验

I0-I3 三个执行 commit（`30032a9` / `683015e` / `7b88fae`）**均未触碰上述 4 个文件**——v3 计划「4 红并入 I0 基线清零，不留执行期自由裁量」未被执行，而 `log-execution.md` I7/dependencies 声称「整仓 ruff check 全绿（清零后为 0）」为虚报。这与本批 `124e9a00`（我已审批通过）的 ruff 4 红遗留是同一批，本实验正是被指派清缴它们的载体——清缴未发生。

## 返工验收（复 complete 时）

- R1 落地 commit：`cli/session_wake_log.py:104` UP038 单行改写 + `ruff check --fix` I001×3（或等价手动修）
- `ruff check .` 整仓复跑 **0 errors**（复跑结果落 execution log，替换/更正原「全绿」虚报声明，注明以清零后实测为准）
- 既有通过项（A1/A3/A7 等）不要求重证
