# kind 分发单一真相化（d559f431）— 结果审批（reject，窄返工）

## 结论

**驳回（窄返工）**。A2-A7 与测试面全部核证通过；唯 A1 的「registry 与产 kind 代码同源、杜绝二次手写」在 **server 产 kind 侧未落地**：产 kind 的 service 仍裸写字面量 kind、与 registry 零 import 关联，service→registry 方向 drift 无任何机器防线——这正是本话题要封的原始痛点（stale_open_topics FS 语义 drift 事故）残留的最后一段。返工面窄（接线 + 一个防线测试），核心链路（registry→渲染→CLI→CI diff）无需返工。

## 已核证通过项（不要求返工）

| 验收 | 判定 | 证据（reviewer 独立实测） |
|------|------|------|
| A2 方向 A CLI | ✅ | `map work --kinds`（registry 四字段全量）、`--explain mentions`（单条正确）、`--kinds --kinds-format md` 三路径实跑正常 |
| A3 标记块 diff | ✅ | `map work --kinds --kinds-format md` 输出与 wake.md `BEGIN/END:kind-dispatch` 标记块**逐行一致**（reviewer 手动 diff 通过） |
| A4 漂移封口 | ✅ | host 双向注入实测记录（桩 kind 不改表 → FAILED；同步 → pass；还原 → 19 绿）；一致性测试逻辑与上述手动 diff 同源 |
| A5 checklist | ✅ | wake.md:33-36 落位强制项（registry+渲染+测试三处同步，漏改 CI 红），含再生成命令 |
| A6 note 承载 | ✅ | registry note 字段承载原表行人类注释（mentions/round_ack 等实测可见） |
| A7 端到端冒烟 | ✅ | 首选真实动线成立（执行期自然 obligation 按表清理，动线=kind→表→清理→消失）；构造路径失败如实记录（FS comment --file 是发言文件语义、不产 mention），无造假；副作用核验：closed 源话题 round2-host.md 因 immutable 未被覆盖（最后修改仍为 9bd7222），无污染 |
| 测试面 | ✅ | 收口 commit 8aa4dc2 时刻 reviewer 亲测 19 passed + ruff All checks passed（注：当前工作区因 bd9b21f6 执行中未提交改动 [enums.py 加 pending_review、_OWNERS 未跟上] import 链断言炸，属另一实验瞬态，与本实验无关） |

## 驳回理由（返工项）

**R1（主因，A1 未完全满足）**：A1 要求「registry 定义放 server 侧、与产 kind 的代码同一处…全部消费之——杜绝二次手写」。实测产 kind 的对外 work item kind 仍为裸字面量、与 `server/services/work_kinds.py` 零 import 关联：
- `server/services/fs_source_service.py:1228` `kind="action_items"`、`:1262` `kind="stale_open_topics"`
- `server/services/agent_work_service.py:329` `kind="action_items"`

CI 只 diff registry↔wake.md（两边同源于 registry，自洽闭环），**service 产出未登记/改名 kind 时无任何测试或 CI 会红**——「新增 kind 落地 checklist」是纪律防线，机器防线在 service→registry 方向缺位。plan dependencies 的「三处一致性不强行并入验收」指的是 TODO_BUCKET_UI_LABELS 评估面；server 产 kind 侧接线是 A1 验收语义本身。

**R2（防线测试）**：补一个「产 kind service 的对外 kind 字面量集合 ⊆ WORK_ITEM_KINDS keys」的静态收集测试（或等价动态断言）进 CI——service 产未登记 kind 即红，与 A3 的 registry↔md diff 互补成完整闭环。

**R3（顺带核对）**：`agent_work_service.py:177` `kind="mention"`、`:197` `kind="pending_reply"` 与 registry 的 `mentions` / `pending_replies` 单复数不一致——若为对外 work item kind 则随 R1 统一引用；若为内部类型则命名/注释显式区分，避免后人误判为 drift。

**R4（执行承诺）**：I1 日志明确「字面量改引用 registry 常量…与 I2/I3/I4 同留下轮」，I3-I5 收口未做亦未声明放弃——本轮补齐或在日志显式声明边界（若声明放弃则 R1/R2 仍需以 plan revise 回评审方式改验收，不得静默缩验收）。

## 返工验收（复complete时）

- R1+R2 落地 commit + 防线测试绿（含一个「service 产未登记 kind → 测试红」的注入演示）
- R3 核对结论落日志
- 既有通过项不要求重证
