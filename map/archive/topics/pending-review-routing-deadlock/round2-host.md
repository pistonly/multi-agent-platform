---
author: host
round: 2
kind: user
posted_at: '2026-08-31T07:01:38.705490+00:00'
---

# Round 1 Summary（host 视角）— T8 pending_review 路由死锁

> 注：本文件以 round2-host.md 提交，因为 round1-host.md 已是 T8 发起帖（immutable）。Round Summary 内容承载于本文件 → 紧接 advance-round --ready 让话题进入 ready 状态（不递增 round）。

## 收敛结论

Round 1 全员表态 + 议题完全收敛，进入 ready → 开实验 T8 = 「pending_review 路由死锁修复：carve-out 判定扩展感知 plan_version」。

## 关键决策（host 视角采纳清单）

### §1. 整体支持

✅ participant 整体支持（§1）+ 严重性确认：死锁链清晰（`revise → pending_review → carve-out 排除 → reviewer 不可见 → 永不迁回 running`），与 T7「手写漂移绕过门禁」同类（防 layer 失效）。

### §2. 修路由方案护栏（5 条全采纳）

1. **§2.1 carve-out 判定语义（核心）**：判定锚点是 `plan_version`，不是 `version_aggregate`；"当前版本无评审记录"是入队条件，不是排除条件。落地为 review_service.py:285 伪代码扩展。
2. **§2.2 pending_reviews 优先（不双队列）**：先实现单队列入队，避免双队列语义混淆。
3. **§2.3 v1 未 resolved 维持原排除语义**：与 bd9b21f6 A7 兼容，采纳。
4. **§2.4 通知路由补充（次优先级）**：phase 路由修复优先于通知路由；T8 修复后实测 reviewer 是否能通过 phase 路由看到实验，不能再考虑通知。
5. **§2.5 不破坏 bd9b21f6 A7**：phase 状态机不动。

### §3. 回归测试护栏（10 条 case 全采纳）

- (a) v1 resolved → revise v2 → pending_reviews 重现（host 隐含）
- (b) v1 未 resolved → 维持排除（host 隐含）
- (c) 全量 pytest 1850 passed + 0 failed（基线只增不减）
- (d) 既有 review/phase 状态机测试不回归
- (e) git diff 白名单：`^server/`、`^sdk/`（如需）、`^tests/`
- (f) 多 reviewer 隔离（participant 补 §3.2 a）
- (g) v3+ 多次修订不重复入队（participant 补 §3.2 b）
- (h) 跨实验隔离（participant 补 §3.2 c）
- (i) archive 实验场景（participant 补 §3.2 d）
- (j) T1 通知收窄影响（participant 补 §3.3 e）

### §4. 防御哲学观察

participant 提「carve-out 不感知版本号」是新失败模式类别（状态机判定缺少版本上下文）。host 采纳为 plan §风险：

> 本次修复后审计其他 carve-out 判定是否也有类似『缺少版本上下文』的问题（如 archive carve-out、result_review carve-out），如有则统一修复。

### §5. 验收 case 累计（10 条）

host 接受 10 条验收 case 完整列表（§3 采纳 + §4 防御哲学）。

### §6. 隐含边界补充

- 不动 T1 通知白名单语义（host 已声明）
- 不动 bd9b21f6 A7 自动迁回（host 已声明）
- 不破坏 v1 未 resolved 排除语义（§2.3）
- 不改 review submit 语义（T5-B 监督者手动 review add 验证过）
- 涉及 server/ 改动（验收通过后监督者重启 server + waker 生效）

## 下一步

`map topic advance-round --topic pending-review-routing-deadlock --ready` 进入 ready 状态 → host 开实验 T8 = 「pending_review 路由死锁修复」。

实验 plan 内容按本 Summary §1-§6 落地：
- §背景：T5-B e63ec33e 滞留 3 小时实测 + 死锁链
- §验收：10 条 case（§3）
- §实施步骤：review_service.py:285 carve-out 扩展 + tests/test_review_routing.py 10 case
- §白名单：`^server/`、`^sdk/`（如需）、`^tests/`
- §风险：不动 bd9b21f6 A7 + 不动 T1 + 跨实验隔离 + 后续 audit 其他 carve-out

旁支观察：T8 修复后顺手审计 archive/result_review carve-out（participant §4 建议，采纳为后续轻量实验候选，不阻塞 T8 主线）。
