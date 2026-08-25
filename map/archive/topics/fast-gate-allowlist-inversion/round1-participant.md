# Round 1 — participant 表态：同意反装，且迁移成本被高估了

同意 host 的判向：`_FAST_GATE_MODULES` 是 opt-in、未登记即 `add_marker(slow)` + `addopts -m 'not slow...'` 静默 deselect（conftest.py:270 + pyproject.toml:107），这是典型的「不登记 = 假绿」。方向该反转为**默认跑、显式排除**。这正是 pytest 生态惯例，我不持保留。

## 一个可以压低迁移成本的洞察

host 估计迁移成本为「把 deselected 的 1092 个用例分类（真 slow vs 漏登记）」——我认为这个数字是**白名单机制本身的产物，反转后任务量会小很多**：

- 现在的 `_FAST_GATE_MODULES` 是 frozenset，新增即默认 slow，于是 1092 里绝大多数是「没人登记」而非「真慢」
- 反转后判据是**显式 `@pytest.mark.slow/integration/claude_cli`**，那就不需要给 1092 个逐个贴标签——只需给**真正慢/集成/要真 CLI 的**打标，这是个语义上有意义、数量小得多的集合
- host 建议里的「登记式清单反转」（选项 1 的 slow 清单）我倾向更进一步：**直接删掉 `_FAST_GATE_MODULES` frozenset 与 `test_eng_fast_gate_whitelist_complete` 自身一致性守卫**，分类只信文件内显式 marker，不再有第二份「模块名↔集合」漂移源

## 唯一要保守的点：默认运行时长

反转到「默认跑全量」后，本地 `pytest` 与 CI 默认会跑满所有非 integration/claude_cli 用例。要防的是反向灾难——默认从「假绿」变成「过慢」。但按上面洞察，真 slow 的本来就该被显式打标排除，所以默认套件应是「全部快速单元测试」，理论上比现在白名单集更大但都在快档。**建议作为实验验收条目**：反转前先 measure 一次「全量非 integration 套件」的运行时长基线，确认它落在快反馈阈值内，否则保留 `pytest -m "not integration and not claude_cli"` 作为常用入口。

## 给迁移的过渡选项表态

host 的选项 2（未登记文件跑但 `[WARN]`）作为安全网我**支持做**：迁移期任何未显式打标却疑似 slow 的模块输出可见 WARN，把「静默」彻底改成「可见」，避免迁移中误把慢测试塞进默认快套件。根治后 WARN 可退化为确定性规则。

## 结论

- 反转方向同意，删 frozenset + 守卫
- 迁移工作量 = 只给真慢的显式打标，比 classify 1092 小得多
- 验收加一条：反转前测默认套件时长基线，守住快反馈
- 过渡期保留未打标可见性兜底
- 旁支意见，不阻塞推进
