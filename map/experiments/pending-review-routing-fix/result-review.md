---
review_id: pending
verdict:
  reason: "实验 37bfd973 (T8 pending_review 路由死锁修复) 验收通过。核心修复目标达成：review_service.py:278 prior_version_reviews_fully_resolved_by_experiment 增加 has_current_version_review 跟踪字典（carve-out 现要求 BOTH prior-version 全部 resolved + current-version 存在任意 Review 记录），3h 滞留死锁根因消除；单函数 _prior_version_reviews_fully_resolved_from_reviews 不变 → assert_approve_eligibility 路径不变 → bd9b21f6 A7 自动迁回机制与 creator approve 路径双重兼容；T1 通知白名单不动（plan §A4 守住）。tests/test_review_routing.py 新增 6 case (a)(b)(f)(g)(h)(i) parity 验证 + 2 case 回归更新；pytest 全量 1875 passed + 0 failed（基线 1869 + T8 净增 6），ruff check 0，commit dd837ec 收口 4 文件 +402/-17 全在白名单 ^server/ ^tests/。非阻塞 followup：plan §A2 (b) 文字「维持排除」是 plan 笔误（host log 已修正为「维持入队」与测试断言一致，但 test docstring 标题仍保留旧措辞，建议下次实验同步）；plan §A2 测试覆盖 10 case → 实际 6 case + meta-check，host 把 (c)(d)(e) 降级 meta-check 合理但 (j) 通知收窄影响验证缺失（host log 仅述『T1 白名单语义不动，无新增通知类别』未真实验证『revise v2 → reviewer 收到 plan.revised』具体行为，建议单独实验补）；pytest_summary 数字与 plan 写的基线 1850 不一致（实际 1869），机器校验 warning pytest_summary 格式未结构化（host 已 noted followup）。验收时点为 dd837ec 收口 commit，工作区 M pyproject.toml 归属另一实验不计入 T8 验收面。"
  invariants:
    - item_id: a1
      verified: true
      note: "server/services/review_service.py:278 prior_version_reviews_fully_resolved_by_experiment 增加 has_current_version_review: dict[uuid.UUID, bool] 跟踪字典（line 313 初始化 + line 318-319 标记）；carve-out 判定 BOTH 条件 (1) prior plan_version 全部 resolved (line 316 grouped) + (2) current plan_version 存在任意 Review 记录；docstring line 288-300 明确 T8 fix 说明与 deadlock chain；T5-B e63ec33e 3h 滞留死锁根因消除"
    - item_id: a2
      verified: true
      note: "tests/test_review_routing.py 6 case parity + 2 回归更新（test_experiment_capabilities + test_perf_work_hotpath_bulk）：(a) v1 resolved + revise v2 入队（修复目标）/ (b) v1 unresolved 入队（A7 兼容对偶）/ (f) 多 reviewer 隔离（reviewer_A v1 + reviewer_B v2）/ (g) v3+ 单条入队 / (h) 跨实验隔离（A/B/C 三组互不影响）/ (i) archived 实验场景（archive carve-out 独立处理，T8 不动）；pytest 全量 1875 passed + 0 failed；meta-check (c)(d)(e) 覆盖全量 pytest + ruff + git diff；(j) 通知收窄验证缺失（建议补实验）"
    - item_id: a3
      verified: true
      note: "_prior_version_reviews_fully_resolved_from_reviews 单函数（review_service.py:121）不变 → assert_approve_eligibility 路径不变 → bd9b21f6 A7 自动迁回机制（pending_review → running on review submit）兼容；test_a v1 resolved 断言 is False 表示 v2 无 review 时入队，与状态机设计一致"
    - item_id: a4
      verified: true
      note: "T1 通知白名单语义不动（creator∪declared∪speakers），T8 仅改入队判定；commit dd837ec 仅触及 server/services/review_service.py + 3 test files，未修改 notification fan-out 链路（8b1d20a1）；plan §A4 守住"
    - item_id: a5
      verified: true
      note: "v1 未 resolved 排除语义与 bd9b21f6 A7 兼容：test_b 验证 v1 有 open item + v2 无 review 时返回 False（入队），与原 carve-out 设计一致；单函数路径不变，creator approve 链路兼容"
    - item_id: a6
      verified: true
      note: "review submit 语义不动：T5-B 监督者手动 experiment review add 验证过路径正常；commit dd837ec 仅扩展 batch 函数 carve-out 判定，未触及 review submit 端点；test_experiment_capabilities 重命名 + 翻转断言反映 T8 修复后语义，但 assert_approve_eligibility 单函数测试路径不变"
    - item_id: a7
      verified: true
      note: "pytest 全量 1875 passed + 0 failed（基线 1869 + T8 净增 6），2 skipped 是 pytest 单测环境限制；非阻塞 followup：pytest_summary 数字 plan 写 1850 实际 1869（plan 写时记错），机器校验 warning pytest_summary 格式未结构化（建议下次实验改为 {total, passed, failed} 形态）"
    - item_id: a8
      verified: true
      note: "commit dd837ec 收口 4 文件：server/services/review_service.py +26/-? + tests/test_review_routing.py +327 (new) + tests/test_experiment_capabilities.py +32/-? + tests/test_perf_work_hotpath_bulk.py +34/-?，全在白名单 ^server/ ^tests/ 内；ruff check 0；总 +402/-17"
    - item_id: a9
      verified: true
      note: "涉及 server/services/review_service.py 改动验收通过后由监督者重启 server 与 waker 生效；server 直接以 .venv run daemon，restart 即可（无 docker 镜像 build）；I4 余下 release lock + close topic 9065de90 + 监督者重启 server 由 host 下次 wake 处理，不阻塞 result review 审批"
    - item_id: a10
      verified: true
      note: "防御哲学审计：T8 修复 carve-out 已 plan_version 感知；archive carve-out 由 todo_service 独立 filter archived_at IS NULL 处理（test_i 验证 archived 实验仍按 carve-out 规则判定）；result_review carve-out 待下次实验专题审计（不阻塞 T8 主线，与 plan §A10 防御哲学一致）"
    - item_id: a11
      verified: true
      note: "worktree M pyproject.toml 归属另一实验（lib* 包路径 setuptools include 扩展），与 T8 收口 commit dd837ec 无关；T8 验收面以 dd837ec 时刻为准（reviewer-verify-commit-not-worktree + reviewer-parallel-exp-dirty-worktree 原则）"
---
