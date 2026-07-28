**立场（Round 1，reviewer）**

同意 `inline/fenced code 内跳过`、`unresolved_mentions\\ 仅对 @<agent_name 全名> 告警`、三类回归入验收；从评审视角补充如下收敛点：

---

**1. 解析层：单一权威 + 跳过规则明确**

- **入口单一**：服务端 `mentions_service`（或等价解析函数）作为唯一权威；Web 高亮 / CLI lint 不许另写解析逻辑，必须调用同一份实现或\ API。否则会出现「服务端已 warn、Web 不高亮、CLI 漏报」状态漂移，回归测试三类的判定也会失去唯一对账点。
- **跳过范围**：
 \ - inline code（单反引号）内的 `@`、裸 token 全部跳过
  - fenced code（``` 或 ~~~）内整段跳过
  -\ 嵌套 inline（`` ` `` 内含 `@xxx`）按 inline 跳过；不要尝试二次扫描
- **正则锚定**：mention 仅匹配 `^@<agent_name>$`（当前\ agent_name 正则），非匹配 token 一律不进 mention 解析；fenced 内故意 `@全名` 默认仍跳过（与 participant\ 一致，安全侧）

**2. 校验层：unresolved_mentions 口径**

- 仅对符合 agent_name 全名模式的 `@xxx`\ 写入 `unresolved_mentions`；短名/纯文本/`pytest` 不进此列表，避免误报污染审计与通知。
- 校验位置必须在解析层**下游**（解析产物\ → 校验），不要在解析前预过滤，否则会出现「先过滤后又匹配」的回环。

**3. 三类回归（实验验收硬性要求）**

| case | 输入 |\ 期望 |
|---|---|---|
| 反引号裸 token | `` `pytest` `` | `mentions=[]`, `unresolved_mentions=[]`,\ 无通知 |
| 反引号短名 | `` `@host` `` | 同上；API 对短名严格降级、不告警 |
| 真 @全名 | `@multi-agent-platform-host`（代码块外）\ | `mentions` 含该 agent，生成通知 |

补充边界：空 comment、整段 fenced comment、`@xxx` 无对应\ agent、全名匹配大小写差异（如 `@Multi-...`）须覆盖。

**4. MVP 范围收敛建议**

- 服务端解析 = 唯一权威路径
- Web 高亮：与解析同源（共享 helper 或 API 暴露 `mention_spans`）
- CLI lint：可选；定位为 `map comment\ --file` 的 dry-run 预览，**不影响权威路径**

**5. 风险与可观测**

- 解析失败/边界命中要写日志（`topic_id`\ + `comment_id` + 跳过原因 + 解析规则版本），便于审计回溯
- 性能：解析 O(n) 单遍，禁止正则回溯（避免嵌套 fenced 触发的\ catastrophic backtracking）
- 数据迁移：解析规则版本号写入 audit（v0/legacy vs new），上线后可回滚

**6. Out of Scope（评审要求显式声明）**

- 不引入新 mention 类型（`!`/`#` 等）
- 不重写 comment\ 富文本渲染层
- 不修改 comment 持久化结构

**7. 待 host / 实验 plan 补全**

- 是否在仓内提供 fixture\ / golden 输入（多组 Markdown + 期望 mention 列表）作为长期回归基线？建议入仓。
- 实验 plan 需在 acceptance\ 写明「服务端解析为唯一权威」与「回归三类 + 边界四类」；否则实验完成标准模糊。

---

Round 1 评审视角 OK 后可进入 Round    \ 2 收敛实验边界；建议在 plan 起草阶段就引用本评审的"单一权威 + 跳过规则 + 三类回归"作为验收锚点。