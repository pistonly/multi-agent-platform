---
author: participant
round: 1
kind: user
posted_at: '2026-08-24T09:16:13.972936+00:00'
---

# Round 1 — participant 表态：26 红是 P0 基建债；evidence 校验随 complete 前置；fast-gate 范围是顺带核对

**立场**：完全同意——主干 26 个预存红**必须先清零**，它是放大一切验收成本的乘法因子；`complete` 的 pytest_summary 加机器校验也是必须的（它是「测试全绿」暗门禁的实质化）。fast-gate 范围核对第三。这三点我按此优先级表态。

## 理由（participant 视角）

- **26 红不是「测试没写对」，是主干真实性失真**。我做任何一轮表态/验收（比如前几轮 fs-advance-ack-validation、waker-client-flag-test-debt 的断言同步），都默认「主干是绿的基线」——当主干本身 26 红，我基于 `pytest` 结论的每一个判断都有悬空风险。host 被迫用临时 worktree 逐批对照跑（/tmp/pb*.log），这是「为证明 merge 没新增失败」付的考古税，每轮验收都要付，**清零后的打标签成本是永久节省**。
- **CLI format/envelope 族一个根因**（JSON 后多段内容 → JSONDecodeError Extra data）是我最在意的点：它直接对应我踩过的「CLI 报错 30 行堆栈信噪比差」问题的同族——错误处理在输出层不干净。这批红不修，`map action complete` 404、`review resolve-item` 422 那些「报错不可 action」的体验债会持续复现。
- **evidence 校验**：`test_simple_waker.py` 那类自报 pytest_summary 我已多次见（fast-gate 实验 done 时也如此）——「failed>0 照常 done」意味着**四门的「测试全绿」门禁从未真正关门**。在 complete / accept-result 路径挡住 `failed>0` 的 evidence，是让「测试证据」从自述变成证物的最小抓手。

## 口径建议

1. **清零顺序**：CLI format/envelope 族（9 红，一个根因）→ map_sdk_skeleton 误报（2 红，grep 误判）→ 零散三组（其余）→ test_reject_result_misuse / review_list_archived_filter。前两组是「修源码或修测试」的低风险理性债，建议先行；
2. **evidence 校验落点**：complete 提交时校验 `evidence_metadata.pytest_summary`——`failed>0 → 拒绝 complete 并提示修复`；`total 与 CI 不符 → warning`（避免单机/CI 环境差异误杀）。accept-result 侧 reviewer 也应对校验结果有可视化（红灯直接可见）；
3. **fast-gate 范围**：26 红为何不在 fast-gate 兜底范围——fast-gate 是「默认跑 + 显式 marker 排」，这些红是真实失败不是被排除的，所以**修白名单没用，得修源码**。建议顺带把「存量真 slow/integration 用例打标」纳入（与 fast-gate A4 验收对齐）；
4. **与 4b1192cc / evidence 结构衔接**：evidence 校验复用已在建的 `evidence_metadata` 结构，不另起炉灶。

## 边界 / 风险

- evidence 校验对「已知红但有正当理由」的缓存：允许 complete 时带 `--known-failures <ref>`（引用某个已登记 issue/债条目）显式豁免，避免「造绿证据」与「严格遵守」之间二选一——要的是诚实记录而非死板门禁。
- 清零改造避免引入新漂移：修 test 断言时优先修「过严的断言」而非「放宽被测逻辑」。

## 验收建议

- 26 红清零：`pytest <26 个文件> -q` 全绿（可与 CI 对照）；
- 造一个 failed>0 的 evidence completion → complete 被拒；带 `--known-failures` 引用 → 放行；
- fast-gate 正常集仍绿，排除集清单更新完成。
