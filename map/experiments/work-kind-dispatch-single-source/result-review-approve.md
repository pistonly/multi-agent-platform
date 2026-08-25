# kind 分发单一真相化（d559f431）— 结果审批（accept，返工复审）

## 结论

**通过（返工复审）**。reject-result（2026-08-24，reviewer）指出的窄返工 R1-R4 已全部落地（commit `6d307cb`），防线测试实测 25 passed + ruff 全绿；reject 时已核证过的通过项（A2-A7、标记块 diff、CLI 冒烟）未被返工破坏，复测确认。

## 返工逐条核验（对照 reject 要求）

| 项 | 要求 | 判定 | 证据 |
|----|------|------|------|
| R1 | 产 kind service 字面量接线 registry | ✅ | `fs_source_service.py:1229` `kind=resolve_kind("action_items")`、`:1263` `resolve_kind("stale_open_topics")`；`work_kinds.resolve_kind`（:136-147）未登记名直接 KeyError（fail-fast 接线） |
| R2 | 防线测试（产 kind ⊆ registry）+ 注入演示 | ✅ | 新增 `tests/test_work_kind_registry_consistency.py`（156 行）AST 静态收集 direction-A service 产 kind 字面量 → ⊆ `WORK_ITEM_KINDS` keys 断言、零豁免（:93-96）；`test_injection_of_unregistered_kind_would_red`（:126）注入演示；**实测 25 passed**（含 work_kinds 16 + cli 3 + 防线套） |
| R3 | 单复数核对（mention/pending_reply vs mentions/pending_topic_replies） | ✅ | 结论：`agent_work_service.py` 的 `SummaryBucketKind` 单数键是**内部聚合桶标签**（/work 摘要卡片），非对外 work item kind，由 SDK Literal 类型约束 + 桶集合断言收口（桶面不得漂出内部类型）；对外分发一律以 registry 复数码为唯一真相——R3 澄清成立，不属 drift |
| R4 | 兑现 I1 承诺或显式声明 | ✅ | I1「字面量接线与 I2-I4 同批」承诺已兑现；防线测试 docstring 显式声明范围边界：DB 话题路径（`topic_work_item_service.py`）内部 kind 单数形为**退役存量面**、经 todo_service 落复数分区键（test_todos.py 已守），不属 direction-A 消费面，无静默缩验收 |

## 复测确认（reject 时已核证项未回归）

- `map --persona reviewer work --kinds` → 12 kind 正常输出（registry 单一真相仍在）
- `--explain stale_open_topics` 正常
- wake.md 标记块逐行一致、checklist 强制项（wake.md:33-36）——返工未触碰 registry 内容/wake.md/CLI，A2-A7 通过项维持
- `ruff check` 四受影响文件 All checks passed

## 遗留（非阻塞）

- 防线测试覆盖 direction-A（fs_source）产 kind service；DB 存量面（退役）依赖存量 test_todos 守住复数分区键，未来 DB 面彻底移除时防线自然退缩，无需本实验处理。
