
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
