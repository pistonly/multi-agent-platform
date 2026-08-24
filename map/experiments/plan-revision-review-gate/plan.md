---
title: "breaking revise 门禁：--breaking-audit 标记由 running 回 pending_review + complete 前真拦截 + complete 时版本核对红旗"
acceptance:
  - "A1 breaking 标记与状态迁移：`map experiment plan revise --breaking-audit`（或 change_note 首行 `breaking:` 前缀）→ phase 由 running 回 `pending_review`，reviewer 的 pending_reviews 队列出现该实验——评审对象必须是最终实践版，不能评一个原地销号的方案"
  - "A2 complete 真拦截（验收主判据）：breaking revise 后 phase 回 pending_review 起，executor `complete` 一律被拒（422/409 + 清晰错误提示「breaking revise 待重评」）；解除信号 = reviewer 对当前 plan_version 提交新评审且其中 open unreasonable items = 0（phase 自动迁回 running）；若新评审仍含 open unreasonable items，complete 持续被拒，报错中列出剩余阻塞项数——executor 可继续跑，但只通知不阻挡等于没回"
  - "A3 非 breaking 留 running：不改 acceptance 条目、不改 phase/承载对象、不改 alembic/migration 级结构的修订 → phase 保持 running、reviewer 队列不变（friction 治理方向：不打断执行流）"
  - "A4 change_note 留痕：breaking revise 的 change_note 必须写明「相对上一版改了什么、为什么」（3d519184 v3 已示范）——作为 reviewer 重评的事实基础；缺失时 revise 命令直接拒绝（单一处置：422 + actionable 报错说明 change_note 应包含「改了什么/为什么」两要素；不设警告分支），报错原文进 evidence"
  - "A5 complete 版本核对红旗（兜底）：complete 时若发现 plan 版本在评审通过后又有架构级修订而未回 review → reviewer 收红旗；防漏标（架构级当笔误级带过）的最后防线"
  - "A6 误标容忍（participant round2 补充）：非 breaking 修订误打了 breaking 标记 → 按「误标」处理——回 review 一次即可，不惩罚、不作红旗事故；门禁容忍一次纠正（负向用例进单测）"
  - "A7 重评解除状态机（评审 item 47abe0dc 定稿）：pending_review 期间 complete 一律拒；`review add` 提交无 open unreasonable 项的新评审 → 解除拦截（pending_review→running）；仍含 open unreasonable 项 → 拦截持续；三个分支均为单测路径"
  - "测试面：状态机迁移（running→pending_review、pending_review→running 解除）、complete 拦截与持续拦截（新评审仍含 unreasonable）、非 breaking 不动、误标路径、版本核对红旗、change_note 缺失拒绝的新增单测全绿；`ruff check` 通过"
evidence_keys:
  - "实测输出：造 running 实验做 breaking revise → phase 回 pending_review + pending_reviews 出现 + executor complete 被拒的报错原文（A1+A2）"
  - "实测输出：非 breaking 修订 → phase 保持 running、队列不变（A3）"
  - "实测输出：breaking revise 缺 change_note → revise 命令被拒的报错原文（A4）"
  - "pytest_summary：状态机迁移与门禁拦截单测全绿（含误标负向用例 A6、重评解除三分支 A7）"
  - "实测输出：评审后未回 review 的架构级修订 → complete 时 reviewer 红旗可见（A5）"
  - "实测输出：pending_review 期间提交无 unreasonable 新评审 → complete 放行；提交仍含 unreasonable 新评审 → complete 持续被拒且报错列剩余阻塞数（A7）"
dependencies:
  - "话题 plan-revision-review-gate（27f385cb-5b43-54a3-b6df-64d0bbdf4e4c）close_note 口径：显式标记回 pending_review + 真挡 complete + 非 breaking 留 running + change_note 留痕 + 版本核对红旗——participant 两轮表态无异议，附补充（误标按误标处理不惩罚）已吸收为 A6"
  - "触发实例：实验 3d519184 running 中 plan v2→v3 架构翻转（close_note 解析+DB 建行 → 收敛落盘 yaml+FS 投影，alembic 051 整条退役）——revise 直接生效而 reviewer 0641c52c 评的是 v2，要到 result_review 才见到 v3；该次变更有用户显式授权、log 留痕、操作正当，但流程未区分「架构级」与「笔误级」revise"
  - "与实验 test-baseline-green-evidence-gate（本批同开）在 complete 门禁同族触碰（close_note 明言「可同批」）：本实验管 plan 版本核对红旗，彼管 pytest_summary 校验——两实验先后落地，后者注意 rebase；同批实现语义见各自 plan 的边界说明"
  - "本实验自身执行期即受新门禁约束：若执行中需架构级 revise 本 plan，走 --breaking-audit 路径自证（自举验收）"
---

# breaking revise 门禁：--breaking-audit 标记由 running 回 pending_review + complete 前真拦截

## 背景

话题 `plan-revision-review-gate`（2026-08-24 实例触发）：实验 3d519184 running 中 plan v2→v3 架构翻转直接生效，reviewer 评的是 v2，要到 result_review 才见到 v3——**reviewer 验收对照物可能是旧版**，评审证据链整个架空；执行者也可能拿着旧版干了一半。流程没有区分「架构级 revise」与「笔误级 revise」，前者不回 review 是治理漏洞。

## 定稿决议（close_note + Round 2 双方表态）

| # | 决议 | 来源 |
|---|------|------|
| D1 | 显式标记，不做 diff 阈值：diff 行数/acceptance 变更数是启发式，会被「大整改语义不变」或「一行语义翻转」绕过；修订者自己声明的 breaking 级别才是诚实信号 | 双方一致（participant round1 立场采纳） |
| D2 | 回 review 必须真挡 complete：只回队列不挡 complete = 只通知不阻挡 = 没回——本话题验收主判据 | participant 口径 2 采纳为硬约束 |
| D3 | 非 breaking 判定（不改 acceptance 条目 / phase/承载对象 / alembic 级结构）留在 running，不打断执行流 | 双方一致 |
| D4 | change_note 留痕：写明相对上一版改了什么、为什么——reviewer 重评的事实基础 | 双方一致 |
| D5 | complete 版本核对红旗兜底：防漏标（架构级当笔误级带过）的最后防线 | participant 边界 1 采纳 |
| D6 | 误标容忍：breaking 判定是主观判断存在误标概率，门禁容忍一次纠正、不把误标当红旗事故 | participant round2 补充采纳 |
| D7 | 重评解除语义定稿：pending_review 期间 complete 一律拒；解除仅由「reviewer 对当前 plan_version 提交无 open unreasonable 项的新评审」触发（pending_review→running 自动迁移）；新评审仍含 unreasonable 则持续拒。无需额外显式动作，判定信号单一可测 | 评审 item 47abe0dc（v1 计划评审）修订定稿 |
| D8 | change_note 缺失处置定稿为「拒绝」：breaking revise 缺 change_note 在 revise 入口即 422 拒绝，不设警告分支——警告无机器可判验收形态，拒绝有明确 422+报错原文可进 evidence；breaking revise 是低频显式治理动作，误打代价低（补 change_note 重试即可），非 breaking 路径不受影响 | 评审 item b9f4ff1e（v1 计划评审）修订定稿 |

## 重评通过与解除语义（A2/A7 状态机，v2 定稿）

```
running --breaking-audit revise--> pending_review（complete 一律拒）
pending_review --review add（无 open unreasonable）--> running（complete 解除）
pending_review --review add（仍含 open unreasonable）--> 保持 pending_review（complete 持续拒，报错列剩余阻塞数）
```

- 解除判定读「当前 plan_version 的最新评审」，不以历史已归档评审为准
- 误标纠正（A6）走同一条迁移：误标 breaking 的 revise 同样回 pending_review，reviewer 重评确认非 breaking 后解除——一次纠正、不惩罚，语义统一在一张状态图里

## 实施顺序（建议，评审可调）

1. **I1 标记与迁移**（A1）：`--breaking-audit` flag / change_note 前缀解析 → running→pending_review 迁移 + 通知 reviewer
2. **I2 complete 拦截与解除**（A2/A7）：pending_review 期间 complete 拒绝（复用 blocked_on 语义或新增门禁字段，实现时按现有状态机最小改动定）；解除 = review add 后 open unreasonable 计数为 0 → 自动迁回 running；仍阻塞则持续拒并在报错列剩余项数
3. **I3 非 breaking 判定**（A3）：默认路径不动 phase；误标纠正路径（A6）
4. **I4 change_note 入口校验**（A4，拒绝处置）+ complete 版本核对红旗（A5）
5. **I5 单测与实测收尾**：六条路径单测（迁移、拦截、持续拦截与解除、非 breaking 不动、误标、红旗、change_note 缺失拒绝）+ 造实验实测 breaking/非 breaking 双动线

## 风险与边界

- running→pending_review 迁移是状态机新增路径：确认与既有 blocked_on / revise 计数 / review_count 归档语义兼容（e8f1b8c1 实验曾处理 review 历史归档），不破坏现有迁移表
- pending_review→running 解除迁移（A7）：解除由 review add 触发自动迁移，需确认与 reviewer 归档旧评审的动作不竞态——实现时把解除判定放在 review add 事务内
- executor 持锁执行中被打回 review：锁语义不受影响（锁是执行并发防护，review 是治理门禁，正交）；执行日志可继续写
- complete 拦截的错误信息需 actionable（提示「等待 reviewer 重评 plan vN（剩余 M 个 open unreasonable 项）」而非通用 409）
- 与 test-baseline-green-evidence-gate 同触 complete 门禁：落地顺序与 rebase 责任见 dependencies

## v2 修订说明（回应评审 72247d89）

- item 47abe0dc：A2 定稿解除信号、新增 A7 与「重评通过与解除语义」状态机小节、测试面与 evidence_keys 补解除/持续拦截分支
- item b9f4ff1e：A4 定稿单一拒绝处置（去「或显著警告」二选一弹性）、evidence_keys 补 A4 实测条目、I4/I5 同步
