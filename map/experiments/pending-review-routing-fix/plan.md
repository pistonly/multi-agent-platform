---
title: "pending_review 路由死锁修复：carve-out 判定扩展感知 plan_version"
acceptance:
  - "A1 server/services/review_service.py:285 carve-out 判定扩展：当前 plan_version 无该 reviewer 评审记录 → 进入 pending_reviews 队列；判定锚点 = plan_version，不是 version_aggregate；『当前版本无评审记录』是入队条件，不是排除条件"
  - "A2 测试覆盖 10 case：(a) v1 resolved → revise v2 → pending_reviews 重现；(b) v1 未 resolved → 维持排除；(c) 全量 pytest 1850 passed + 0 failed；(d) 既有 review/phase 状态机测试不回归；(e) git diff 白名单 ^server/ ^sdk/ ^tests/；(f) 多 reviewer 隔离；(g) v3+ 多次修订不重复入队；(h) 跨实验隔离；(i) archive 实验场景；(j) T1 通知收窄影响"
  - "A3 不动 bd9b21f6 A7 自动迁回机制（phase pending_review → running 状态机设计正确，T8 只改入队判定）"
  - "A4 不动 T1 通知白名单语义（creator∪declared∪speakers；T8 通过修复 phase 路由解决问题，不扩大白名单绕过）"
  - "A5 不破坏 v1 未 resolved 排除语义（与 bd9b21f6 A7 兼容）"
  - "A6 不改 review submit 语义（T5-B 监督者手动 review add 验证过路径正常）"
  - "A7 pytest_summary：基线 1850 passed + 本实验净增 10 case = 1860 passed，0 failed"
  - "A8 git diff 白名单：^server/、^sdk/（如需）、^tests/，无其它路径"
  - "A9 涉及 server/ 改动验收通过后由监督者重启 server 与 waker 生效（server 直接以 .venv run daemon；restart 即可；无 docker 镜像 build）"
  - "A10 防御哲学：本次修复后审计其他 carve-out 判定是否也有类似『缺少版本上下文』的问题（archive carve-out、result_review carve-out），不阻塞 T8 主线"
evidence_keys:
  - "实测输出:(1) `map --persona reviewer work` 在 v1 resolved + revise v2 后 pending_reviews 队列出现该实验；(2) `map --persona reviewer work` 在 v1 未 resolved + revise v2 后 pending_reviews 队列不出现该实验（维持排除）；(3) 监督者手动 `map --persona reviewer experiment review add` 后 phase 仍按 bd9b21f6 A7 自动迁回 running（确认状态机未受影响）"
  - "grep 核证:server/services/review_service.py:285 `prior_version_reviews_fully_resolved` 函数扩展 plan_version 上下文感知；tests/test_review_routing.py 含 10 case (a)-(j) fixture；server/services/review_service.py 无其它 carve-out 改动（仅扩展一处）"
  - "pytest_summary:tests/test_review_routing.py 10 case 全过 + 全量 pytest -q 0 failed（基线 1850 passed 只增不减，本实验净增 = 10 case）"
dependencies:
  - "话题 pending-review-routing-deadlock（slug）Round 1+2 全员表态 ready；description『revise plan v2 后 carve-out 误排除，reviewer 不可见永不迁回 running；修路由+回归测试』"
  - "T5-B 实验 e63ec33e 滞留 3 小时实测（host round1-host.md §现象）：revise plan v2 → phase=pending_review → reviewer work 报 pending_reviews=[] → carve-out 排除根因"
  - "既有 server/services/review_service.py:285 prior_version_reviews_fully_resolved 函数（修复对象）"
  - "既有 bd9b21f6 A7 自动迁回机制（reviewer 提交 review 后 phase 自动从 pending_review 迁回 running，不动）"
  - "既有 T1 通知收窄（8b1d20a1）白名单语义 creator∪declared∪speakers（不动，仅验证兼容）"
  - "既有 T5-A (715202a3) waker 巡检视图 + T5-B (e63ec33e) per-实验成本台账 + T6 (a8b64c20) 阈值派生 已落地链路无冲突；T8 是『实验路由可见性』层与 T7 verify-audit『数据漂移检测』层互补"
  - "T8 修复后审计其他 carve-out 判定（archive / result_review）是否也有类似『缺少版本上下文』问题（防御哲学观察，不阻塞 T8）"
whitelist:
  - "^server/"
  - "^sdk/"
  - "^tests/"
---

# T8 pending_review 路由死锁修复：carve-out 判定扩展感知 plan_version

## 背景

T5-B 实验 `experiment-cost-ledger` (e63ec33e-7f3d-4a89-a34d-33a584924307) 在 I0 spike 完成（数据源假设错误：`.map/runtime-waker-sessions/*.jsonl` 不含 token usage；真实源是 `.map/claude-runtime-home-{host,participant,reviewer}/.claude/projects/.../*.jsonl` 含 type=assistant + message.usage{input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens}）→ host 提交 plan v2 修订 → 实验 phase 切到 `pending_review` (phase_owner=reviewer)。**此后 3 小时 reviewer 未接手**：

- `map --persona reviewer work` 的 `pending_reviews: []`、`pending_plan_revisions: []`——实验从 reviewer 队列消失；
- 监督者手动 `map --persona reviewer experiment review add`（无阻塞项）后，phase 按设计自动迁回 running——证明状态机本身没坏，断的是「进入 reviewer 视野」这一步。

**死锁链**：`revise → pending_review → carve-out 排除 → reviewer 不可见 → 无人提交 v2 评审 → 永不迁回 running`。

## 根因

`server/services/review_service.py:285` 的 `prior_version_reviews_fully_resolved` carve-out 判定：**不区分**「v1 review 已 resolved」与「v2 新版本尚未评审」——revise plan 产生 v2 后，pending_review 的语义是「等 reviewer 重评 v2」，但 carve-out 把实验排除出了 `pending_reviews` 队列。

## 验收（10 条 case）

### 路由主路径（5 条）

- (a) **v1 resolved → revise v2 → pending_reviews 重现**：fixture 构造 v1 review 全 resolved → host 调 `experiment plan revise` 提交 v2 → phase 切 pending_review → 断言该实验出现在 reviewer 的 `pending_reviews` 队列
- (b) **v1 未 resolved → 维持排除**：v1 review 仍有 unresolved 项 → revise v2 后实验**不**进入队列（与 bd9b21f6 A7 兼容）
- (c) **全量测试 1850 passed + 0 failed**：本实验净增 10 case，全量 `pytest -q` 基线只增不减 0 failed
- (d) **既有 review/phase 状态机测试不回归**：`tests/` 中 review_service、phase_service、experiment_lifecycle 相关测试全部继续 pass
- (e) **git diff 白名单**：`git diff --name-only main` 产物 ∈ {server/, sdk/, tests/}，无其它路径

### 兼容性护栏（4 条，participant §3.2 补）

- (f) **多 reviewer 隔离**：v1 由 reviewer_A resolved → revise v2 → reviewer_B 应看到该实验（即使 reviewer_A 已不接收通知）→ 验证"按 reviewer × plan_version"组合判定
- (g) **v3+ 多次修订不重复入队**：v1 resolved → v2 resolved → v3 pending → reviewer 队列仅出现一次该实验 → 验证"按当前 plan_version 单条入队"
- (h) **跨实验隔离**：experiment_A 的 v1 resolved + revise v2 不影响 experiment_B 的 v1 pending 判定 → 验证实验级别隔离
- (i) **archive 实验场景**：experiment 已 archived（pending_reviews 排除 archive）→ 验证 T8 修复不影响 archive carve-out 语义

### 通知路由关联（1 条，participant §3.3 补）

- (j) **T1 通知收窄影响**：v1 review 提交 → revise v2 → 验证 reviewer 是否收到 `plan.revised` 通知；如果 phase 路由修复后 reviewer 仍不处理，再考虑通知路由增强

## 实施步骤

### I1 server/services/review_service.py:285 carve-out 扩展

修改 `prior_version_reviews_fully_resolved` 判定，新增 plan_version 上下文感知：

```python
# 伪代码扩展（实际实现细节由 executor 落地）
def should_include_in_pending_reviews(experiment, reviewer):
    current_version = experiment.current_plan_version
    
    # 当前 plan_version 是否有该 reviewer 评审记录？
    has_review_record = Review.query.filter_by(
        experiment_id=experiment.id,
        plan_version=current_version,
        reviewer_id=reviewer.id,
    ).exists()
    
    if has_review_record:
        return False  # 已评审当前版本，不在队列
    
    # v1 已 resolved 但当前 v2 未评审 → 仍需进入队列
    # 默认返回 True（包括 v1 未 resolved 场景）
    return True
```

关键护栏：
- 判定锚点是 `plan_version`，不是 `version_aggregate`
- "当前版本无评审记录"是入队条件，不是排除条件
- v1 未 resolved 时维持原排除语义（与 bd9b21f6 A7 兼容）

### I2 tests/test_review_routing.py 10 case

按 §验收 (a)-(j) 落地 fixture：
- 直接 mock experiment + review 记录，验证 carve-out 函数返回值
- 或 FastAPI TestClient + sqlite:///:memory: 端到端 fixture（具体由 executor 评估）

### I3 全量 pytest + ruff check + narrow commit + log

- `python -m pytest tests/ -q` 全绿
- `ruff check server/ tests/ sdk/` 全绿
- 窄 commit: `map exp <short-id>: <summary>`
- 写 experiment log（I1 / I2 / I3 各一条）

### I4 实验完成 → reviewer 评审 → release lock

- 监督者重启 server（涉及 server/ 改动）
- reviewer 评审 plan / 验收 result
- host release lock + 关闭话题 `pending-review-routing-deadlock`

## 风险与边界

- **不动 bd9b21f6 A7 自动迁回机制**：phase 状态机设计正确，T8 只改入队判定
- **不动 T1 通知白名单语义**（creator∪declared∪speakers）：T8 通过修复 phase 路由解决问题，不扩大白名单绕过
- **不破坏 v1 未 resolved 排除语义**：与 bd9b21f6 A7 兼容
- **不改 review submit 语义**：T5-B 监督者手动 review add 验证过 review submit 路径正常
- **涉及 server/ 改动**：验收通过后由监督者重启 server 与 waker 生效
- **后续审计（防御哲学观察）**：本次修复后审计其他 carve-out 判定是否也有类似『缺少版本上下文』的问题（如 archive carve-out、result_review carve-out），如有则统一修复——不阻塞 T8 主线

## 证据

- T5-B e63ec33e 滞留 3 小时实测（话题 `pending-review-routing-deadlock` round1-host §现象）
- `server/services/review_service.py:285` `prior_version_reviews_fully_resolved` 函数（待 executor 实际定位确认）
- bd9b21f6 A7 自动迁回设计文档（不变，仅验证兼容）
- T1 通知收窄（8b1d20a1）白名单语义（不变，仅验证兼容）

## pytest_summary

基线 1850 passed (T6 闭环) + 本实验净增 10 case (a)-(j) = **1860 passed**，0 failed。

## 主题联动

话题：`pending-review-routing-deadlock` (slug) → uuid5 deterministic id（由 `--topic-id` 解析）

防御哲学（与 T7 同步）：
- T7 verify-audit = 检测层（audit 链一致性）
- T8 = 路由层（实验可见性）
- 共同点：都是"防 layer 失效"——T7 防手写漂移绕过门禁，T8 防 carve-out 不区分版本号导致实验从 reviewer 视野消失
