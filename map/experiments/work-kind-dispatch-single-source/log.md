
## 2026-08-24 15:40 · I1 完成（registry 落位）

- `server/services/work_kinds.py`：WorkItemKindSpec 四字段（kind/clear_action/skill/note）+ WORK_ITEM_KINDS 12 条——从 wake.md 分发表逐行迁移（合并行拆独立条目：pending_reviews/pending_result_reviews/pending_replies；人类注释按 D8 迁 note 字段；「未读通知」行非机器 kind 未收录）
- `tests/test_work_kinds.py` 6 组 16 用例：kind 唯一、skill/clear_action 非空（A1 斅链防线）、get 查询、registry == wake.md 表首列 kind 集合（A3 CI diff 雏形，提取逻辑去括号内 reason 修饰值）、skill ∈ 已知 Skill 集
- 全绿 + ruff check 过；commit: map exp d559f431 I1
- 边界：产 kind 三处（topic_work_item_service / agent_work_service / fs_source_service）的字面量改引用 registry 常量属消费面接线，与 I2（map work kinds CLI）/I3（CI 标记块整行 diff）/I4（wake.md 渲染切换）同留下轮——风险边界已声明渐进覆盖
