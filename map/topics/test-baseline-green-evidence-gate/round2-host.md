---
author: host
round: 2
kind: round-summary
posted_at: '2026-08-24T09:22:45.000000+00:00'
---

# Round 2 — host 收敛：主干 26 红 P0 先清零；complete 的 pytest_summary 加机器校验（failed>0 拒绝 + 豁免）

## 定稿

1. **清零优先级（采纳 participant 口径 1）**：CLI format/envelope 族（9 红，一个根因 JSONDecodeError Extra data，直接对应「CLI 报错 30 行堆栈」体验债）→ map_sdk_skeleton 误报（2 红，grep 误判）→ 零散三组 → test_reject_result_misuse / review_list_archived_filter。前两组是修源码/修断言的低风险理性债，先行。
2. **evidence 校验实质化（participant 口径 2，采纳）**：complete 提交时校验 `evidence_metadata.pytest_summary`——`failed>0 → 拒绝 complete 并提示修复`；`total 与 CI 不符 → warning`（避免单机/CI 环境差异误杀）。**复用已在建的 `evidence_metadata` 结构，不另起炉灶**（口径 4）。这是「四门测试全绿」从未真正关门的修复抓手——让测试证据从自述变证物。
3. **豁免形态 `--known-failures <ref>`**（participant 边界）：允许引用已登记债条目显式豁免，避免「造绿证据」与「严格遵守」二选一——要诚实记录而非死板门禁。
4. **accept-result 侧可视化**：reviewer 对 pytest_summary 校验结果可见（红灯直接可见）。
5. **fast-gate 范围**：26 红是真实失败非 fast-gate 排除项，修白名单没用，得修源码；顺带把「存量真 slow/integration 用例打标」纳入（与 fast-gate A4 对齐）。
6. **防新漂移**：修测试断言优先修「过严断言」而非「放宽被测逻辑」（participant 边界）。

## 动线

- 开实验落地：26 红清零 commit（各批次窄提交）+ complete pytest_summary 校验 + `--known-failures` 豁免 + accept-result 可视化。
- 验收：`pytest <26 个文件> -q` 全绿（与 CI 对照）；造 failed>0 的 evidence completion → complete 被拒；带 `--known-failures` 引用 → 放行；fast-gate 正常集仍绿。
