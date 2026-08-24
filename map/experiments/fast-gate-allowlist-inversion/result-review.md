# fast-gate 白名单反转 — 结果审批（accept）

## 结论

**通过**。计划 acceptance A1–A5 与测试面逐条满足，commit 收口完整，变更面干净。

## 验收逐条核验

| 验收 | 判定 | 证据 |
|------|------|------|
| A1 基线测量 | ✅ | baseline.md：全量 1689/1703 例、1612.80s、52 failed + per-duration 局部 + 52 失败 triage 分类 |
| A2 假绿消除 | ✅ | 探针（无 marker 必败）默认收集并失败、验证后删除；`test_fast_gate_inversion.py` 以 stub tmp_path 双文件持久断言「无 marker 默认收集 / 显式 marker 排除」——实测复跑 **5 passed** |
| A3 删除完成 | ✅ | git HEAD 版 `tests/conftest.py` grep 无 `_FAST_GATE_MODULES` / `pytest_collection_modifyitems`；`test_eng_fast_gate_whitelist_complete.py` 已从 HEAD 移除 |
| A4 存量分类 | ✅ | 反转后 `--collect-only`：1323 collected / 357 deselected（= 显式 marker slow/integration/claude_cli 数，37 文件 pytestmark），无静默残留 |
| A5 时长达标（兜底分支） | ✅ | 反转后默认套件 1323 例 818.23s / 26 failed（既有债务非回归）；兜底入口 `pytest -m "not integration and not claude_cli"` 记录于 tests/README.md，CI 矩阵已更新为「反转后真实收集」语义 |
| 测试面 | ✅ | test_fast_gate_inversion 5 项单测实测全绿；ruff check 通过（r1 log） |

## 关键核验点

1. **commit 收口完整**：`476aa19`（I2–I5 反转 + 单测）与 `8b6e488`（A5 兜底文档）均为 HEAD 祖先；--stat 显示改动仅限 conftest / 守卫 / 新增单测 / README / baseline / review.yaml，**未触碰任何测试逻辑文件** → 反转后 default suite 的 26 个失败不可能是本实验引入的回归（均系反转暴露的既有债务，baseline 52 failed triage 与 A5 复测同源）。
2. **红 = 真实状态**：反转的目标就是消除假绿；反转后默认套件 818s / 26 failed 是假绿消除的必然代价（旧 90s 阈值是白名单 603 例子集假象），A5 兜底分支已按计划落地，符合计划「风险与边界」预期。
3. **r2 认证事故为环境级**：影子 server 空库顶替导致 401 与 config 污染，host 已切回旧库 + reissue 三 persona token 恢复，与实验代码无关，不影响结果判定。
4. **跨界确认**：与 cli-hygiene-batch（207d7c4b）的时序交互已正向解除——反转先行落地，后者执行时新测试文件默认收集，无需临时登记白名单。

## 遗留（非阻塞，已由实验明确归出范围）

- 26 个既有失败债务（test_cli_error_envelope / test_error_codes_cli / test_map_sdk_skeleton / test_m2_flow 等）不修，属反转暴露、建议后续话题分「环境性 fixture 隔离」与「API 演进跟修」两批收敛。
