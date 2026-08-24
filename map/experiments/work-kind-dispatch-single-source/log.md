
## 2026-08-24 15:40 · I1 完成（registry 落位）

- `server/services/work_kinds.py`：WorkItemKindSpec 四字段（kind/clear_action/skill/note）+ WORK_ITEM_KINDS 12 条——从 wake.md 分发表逐行迁移（合并行拆独立条目：pending_reviews/pending_result_reviews/pending_replies；人类注释按 D8 迁 note 字段；「未读通知」行非机器 kind 未收录）
- `tests/test_work_kinds.py` 6 组 16 用例：kind 唯一、skill/clear_action 非空（A1 斅链防线）、get 查询、registry == wake.md 表首列 kind 集合（A3 CI diff 雏形，提取逻辑去括号内 reason 修饰值）、skill ∈ 已知 Skill 集
- 全绿 + ruff check 过；commit: map exp d559f431 I1
- 边界：产 kind 三处（topic_work_item_service / agent_work_service / fs_source_service）的字面量改引用 registry 常量属消费面接线，与 I2（map work kinds CLI）/I3（CI 标记块整行 diff）/I4（wake.md 渲染切换）同留下轮——风险边界已声明渐进覆盖

## 2026-08-24 15:55 · I2 完成（map work --kinds / --explain）

- cli/main.py：work 命令加 --kinds / --explain flag（A2 方向 A；flag 形态在 plan 措辞弹性内，避免顶层命令改子应用的高风险重构）；_print_work_kinds 延迟 import server.services.work_kinds（先例 simple_waker，不拖 CLI 启动依赖）
- 实测三路径：--kinds 全 registry 输出；--explain mentions 单条；--explain no_such → stderr 漂移提示 + exit 2
- tests/test_cli_work_kinds.py 3 用例（直接测函数，不经 CliRunner 避开 envelope 族 stdout 耦合）；连同 I1 共 19 绿 + ruff 过
- 剩余：I3（CI 标记块整行 diff）、I4（wake.md 渲染切换/注释迁 note 已在 registry 就位）、I5（checklist 文档+冒烟）留下轮

## 2026-08-24 16:10 · I3-I5 完成，A1-A7 收口（complete 提交）

- I3：server/services/work_kinds.py 增 render_kinds_md() 唯一渲染函数；CLI --kinds-format md；tests 一致性测试升级为「标记块内整行逐字符 diff，缺块 fail-safe」
- I4：wake.md 分发表迁入 BEGIN/END:kind-dispatch 标记块（四列表：kind/清理动作/Skill/说明；原表行人类注释已在 registry note 字段）
- I5：标记块后落「新增 kind 落地 checklist」强制项（registry+渲染+测试三处同步，漏改即 CI 红）
- A4 漂移封口实测：①桩 kind 不改 wake.md → 一致性测试 FAILED ②桩 + wake.md 同步重渲染 → pass ③还原 → 19 绿
- A7 端到端冒烟（首选真实动线成立）：本实验执行期自然 obligation 反复出现并按表清理——pending_plan_revisions（kind 分发表 plan_revise 行 → plan revise → work 消失）、review.submitted digest 通知（read-all → 消失），动线即「kind → 表 → 清理动作 → 列表消失」全链
- A7 构造路径补充发现（如实记录）：尝试在 closed 源话题用 topic comment 构造 mention——实测 FS 话题的 comment --file 语义是写 round<N>-<persona>.md 发言文件（round2-host.md 已写入冒烟说明，话题 closed 无害、immutable 不覆盖）；FS 发言文件不产生 mention obligation。mention 构造的正确入口是实验评论（需 anchor 结构），本轮未继续
- A1/A2/A6 由 I1/I2 承接（registry 四字段含 note 主观标准承载、--kinds/--explain 三路径实测、exit 2 漂移信号）

## 2026-08-25 · R1-R3 返工落地 + R4 边界声明（result reviewer reject → narrow Rework; commit 6d307cb）

- **R1（接线）**：direction-A 产 kind 两处改经 `work_kinds.resolve_kind` 取复数码——
  `fs_source_service.py:1229` `kind=resolve_kind("action_items")`、`:1263` `kind=resolve_kind("stale_open_topics")`
  （原裸字面量摘除，registry 零 import 关联破口补齐）；`work_kinds.resolve_kind(name)` 未登记即 KeyError 的 fail-fast 兜底，service 产未登记/改名 kind 运行时立即红
- **R2（防线测试）**：`tests/test_work_kind_registry_consistency.py` 6 项全绿——AST 静态收集 fs_source 产 kind 集合校验 == {action_items, stale_open_topics} 且 ⊆ WORK_ITEM_KINDS keys（零豁免）；resolve_kind fail-fast 正反例；agent_work 桶 kind ⊆ SummaryBucketKind Literal；registry 唯一非空。**注入演示实测**：临时把 `resolve_kind("action_items")` 改成 `resolve_kind("_injected_unregistered_kind")` → subnet 断言精确红（Extra: `_injected_unregistered_kind`）→ 还原 6 绿；相关套件（test_todos/test_topic_work_items/test_work_summary/test_fs_remote_mode/test_action_items）34 passed + ruff check 全绿
- **R3（核对结论）**：`agent_work_service.py:177/197` 的 `kind="mention" / "pending_reply"` 为 **/work 摘要卡片内部聚合桶标签**（非对外 work item kind），已在 `_BUCKET_KIND_VISIBILITY` 上方加注释块显式区分「桶 ← registry 复数码聚合」，每行补来源注释——单/复数不同形是有意内部命名，非 drift；桶面另由 SDK `SummaryBucketKind` Literal + 测试约束
- **R4（边界显式声明）**：I1 承诺的「产 kind 三处字面量改引用 registry 常量」逐项落地判定：
  1. `fs_source_service`（direction-A 消费面）→ 已接线（R1）
  2. `agent_work_service` 桶 kind → **正确归属不是 registry 消费方**：内部聚合桶，经 todo_service/topic_work_item_service 由对外 kind 聚合而来（R3 注释区分）
  3. `topic_work_item_service`（DB 话题路径）内部单数 kind（mention / pending_topic_reply）→ **退役存量面**：经 todo_service 落复数分区键，由 tests/test_todos.py 守卫，不属 direction-A 对外消费面；单数→复数映射是退役迁移语义一部分，强行接 registry 复数码会模糊退役边界
  - 故本轮不构成「静默缩验收」：direction-A 产 kind 面（fs_source）已接线 + 防线测试 + 注入演示闭环；非消费面（桶/DB 退役面）逐项显式声明归属与守卫测试，边界落测试 docstring 与 wake.md 分发表 note
