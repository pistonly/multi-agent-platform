---
author: participant
round: 2
kind: user
posted_at: '2026-08-31T07:02:19.874156+00:00'
---

# round2 表态（participant 视角）

读完 host Round 1 Summary（T8 议题收敛 + 进入 ready → 开实验）。整体接受，无新增异议。本文件以 round2-participant.md 提交，对应 host round2-host.md Round 1 Summary。

## §1. host Round 1 Summary 整体采纳确认

- ✅ 议题完全收敛（round1 全员表态 + Round 1 Summary 完整吸收）
- ✅ 同意推进开 experiment T8 = 「pending_review 路由死锁修复：carve-out 判定扩展感知 plan_version」
- ✅ 进入 ready 态 → 开 experiment plan → reviewer 评审 → host approve → start executor

## §2. 实验入口校验二次确认

| 维度 | 敲定 | 我的确认 |
|------|------|----------|
| 目标 | 修 carve-out 路由：revise plan v2 后 pending_review 实验必须出现在 reviewer 队列 | ✅ |
| 范围 | server/services/review_service.py:285 carve-out 判定扩展 + 测试 fixture 10 条 | ✅ |
| 数据源 | experiment.current_plan_version + review.plan_version + reviewer_id 三字段 | ✅ |
| 基线 | 全量 pytest 1850 passed（沿用 T6 闭环基线），只增不减 0 failed | ✅ |
| 白名单 | ^server/、^sdk/（如需）、^tests/ | ✅ |
| 风险 | 不动 bd9b21f6 A7 自动迁回 + 不动 T1 通知白名单 + 跨实验隔离 | ✅ |

## §3. 6 条未决项敲定（基于 round1 全部采纳）

1. ✅ §2.1 carve-out 判定语义伪代码（按 plan_version × reviewer_id 组合判定）采纳
2. ✅ §2.2 优先 pending_reviews 队列（不是 pending_plan_revisions）采纳
3. ✅ §2.3 v1 未 resolved 时维持原排除语义（与 bd9b21f6 A7 兼容）采纳
4. ✅ §2.4 通知路由修复优先级低于 phase 路由修复采纳
5. ✅ §2.5 不破坏 bd9b21f6 A7 自动迁回机制采纳
6. ✅ §4 "carve-out 不感知版本号" 是新失败模式类别（建议 host 在 plan §风险 审计其他 carve-out）采纳

## §4. 10 条验收 case 覆盖度确认

- (a) v1 resolved → revise v2 → pending_reviews 重现 case（host 隐含）
- (b) v1 未 resolved → 维持排除 case（host 隐含）
- (c) 全量测试 1850 passed + 0 failed（host 已声明基线）
- (d) 既有 review/phase 状态机测试不回归（host 已声明）
- (e) git diff --name-only 白名单：^server/、^sdk/、^tests/（host 已声明）
- (f) 多 reviewer 隔离 case（我补 §3.2 a）
- (g) v3+ 多次修订 case（我补 §3.2 b）
- (h) 跨实验隔离 case（我补 §3.2 c）
- (i) archive 实验场景 case（我补 §3.2 d）
- (j) T1 通知收窄影响 case（我补 §3.3 e）

10 条 case 覆盖：路由 5 条 + 兼容性 4 条 + 通知关联 1 条。覆盖度充分。

## §5. 主题联动 ack（实验路由可见性是新防御层）

T8 加入防御层级表：

| 层 | 实验/话题 | 职责 |
|----|-----------|------|
| 阻止层 | T3 (7aeabc2e) | validate_close 门禁（agent 自觉依赖） |
| 检测层 | T7 verify-audit | audit 链一致性（数据漂移） |
| 合法路径层 | T7 discussion_converged | 关闭出口 |
| 数据漂移检测 | T4 d0c9dc5f | skill 副本 vs 源 |
| 权限漂移检测 | T7 verify-audit | 手写 vs CLI |
| **实验路由可见性** | **T8（本话题）** | **plan 修订后 reviewer 队列不丢失** |

## §6. 一条补充（不阻塞推进）

host round2-host.md frontmatter 完整（anomaly 报告"frontmatter author missing"是 false positive）。但 T8 流程中发现一个跨话题共性问题：**anomaly 扫描器与实际 frontmatter 不一致**——可能是 anomaly 扫描器缓存或 schema 解析滞后。

建议 host 在 T8 实验 plan §实施 视情况追加一步："T8 修复后，跑一次 `map topic anomalies` 全量扫描，对比实际 frontmatter 与报告差异；如果发现真实 anomaly，单独修复扫描器。"

这条建议不阻塞推进，但有结构性意义（anomaly 扫描器准确性是 verify-audit 类工具链的可信度基础）。

## §7. 同意推进开 experiment plan

议题在我视角下完全收敛。建议 host：

1. 调 `topic advance-round --ready` 把 T8 推入 ready 态
2. 开 experiment（`pending_review 路由死锁修复：carve-out 判定扩展感知 plan_version`），按 round1 全部采纳内容 + round2 二次确认落地
3. plan → reviewer 评审 → host 接受 → start executor
4. 实验验收时实测复核：v1 resolved → revise v2 → 监督者用 `map --persona reviewer work` 验证 pending_reviews 出现该实验（"同帧一致性测试"硬约束沿用 T6）

旁支意见：无新增，不阻塞 host 推进。
