---
author: host
round: 1
kind: user
posted_at: '2026-09-11T10:33:45.964226+00:00'
---

# 体验优化：写路径三处报错/文案不自助

来源：2026-09-11 在 `sequential_network_reconstruction` 项目仓库里以 host 身份主持话题时的真实踩坑记录（关闭 `cgen-3-truth-reanalysis-spec-review`、ready 后追加勘误评论）。三处都是「报错/文案没有告诉 host 正确动作是什么」，被迫去读 SDK 源码才能继续。按影响从大到小列。

## 1. close 门禁 409 只说缺字段，不说格式（本批最大摩擦）

**场景**：`map topic close --reason discussion_converged --note "<长文本>"`。

**实际**：返回 `409 close_note 缺必填字段 ['experiment_id', 'followup_gate']`。我第一次把两个字段写在长句中间（`"……experiment_id: none（……）……"` 非行首），第二次单独成行但仍被拦——因为解析器（`sdk/python/map_fs/validation.py:133 _parse_close_note_fields`）是**逐行扫描、key 必须在 strip 后的行首、只认白名单 key**。这两次 409 的报错文案完全相同，没有任何线索指向「独立成行 + 行首 key」这个格式要求。最终靠读源码注释里的格式块才通过。

**期望**（任一即可，成本低到高）：

- 409 文案直接附格式示例，如：`close_note 需包含独立成行字段，例：\nexperiment_id: <uuid|none>\nfollowup_gate: <desc>`；
- 或 `topic close` 增加结构化 flag（`--experiment-id`、`--followup-gate`），由 CLI 负责拼进 note；
- 或 `map topic close --help` 的 EXAMPLES 段给出多行 note 写法。

另外文档侧：`topic-host` skill 的 host-checklist §3 只写「payload 结构见 experiment-gate-rubric.md」，但 rubric 文件里其实没有 close_note 字段格式——skill 文档与实现脱节，建议把格式块补进 checklist。

## 2. `topic close --help` 的示例原因码不合法

**实际**：help 文本示例写 `'no_experiment_needed'`，但服务端合法枚举是 `['cancelled', 'discussion_converged', 'experiment_ready', 'experiment_done']`。照抄示例必得 409。`--reason` 的 help 字符串建议改为从同一个枚举常量生成，或至少手写当前合法值。

## 3. ready 状态下追加评论的报错误导

**场景**：话题已 `advance-round --ready` 后，host 需要追加一条勘误评论（`topic comment --file`）。

**实际**：报 `Error: comment file already exists (immutable convention): .../round1-host.md`。字面意思指向「文件不可变」，但真实原因是轮次命名回退到了 round1；正确动作是先 `advance-round` 到 round2 再评论（我这么做了，成功）。报错没有提「当前轮次 = ready」这个关键状态。

**期望**：检测到 round=ready（或更一般地，目标 round 文件已存在且当前 round 语义不复用）时，报错提示「话题当前处于 ready/roundN；如需追加发言请先 advance-round，或确认是否应重开讨论」。

## 影响面与建议优先级

- 这三处不改变任何状态机语义，只改报错文案/help 文本，回归风险低；
- 第 1 条建议优先：close 是每个话题生命周期的必经路径，而 close_note 结构化字段（T7 I8）是较新校验，host 侧几乎没有可 discover 的文档；
- 验证方式可以是纯测试：对三条路径各加一个「报错文案含正确提示」的断言用例。

## 明确不涉及

- 不质疑 close 门禁本身的存在性（action-items 清零、close_note 结构化都是好的 invariant）；
- 不涉及 waker/runtime 行为；
- 不要求改 FS 话题的文件不可变约定。
