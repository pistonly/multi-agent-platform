# M55 log

## Round 0：计划创建（2026-08-16）

### 创建过程

- 计划落盘 `map/experiments/m55-actionable-error-envelope/plan.md`（frontmatter：title / acceptance×7 / evidence_keys×3 / dependencies×2）
- 依赖核实：v0.12 提案话题 round2 已收敛 ready（reviewer 两轮核对，`d669e72`）；M54 实验 done（6cf0a74）——CLIErrorEnvelope 与 docs/cli-json-output.md 契约可用作 M55E 渲染基座
- 创建命令（全量 `--plan-file` 路径，E8 未修瘦身模式仍会误伤）+ `--submit-for-review`：`84cccb2e-2a69-4339-9dfd-c3df1bf23d31`，phase=review
- **M54 交付即时实证**：`--format json | jq -r '.data.id'` 一行取 id/phase，M54 Round 0 的 E1 痛点（顶层结构未知、`'str' object has no attribute 'get'`）不复存在

### 创建踩坑（新发现，范围外）

- 尝试 `--topic-id b3b383a1-...`（v012-ergonomics-review）关联话题被拒：`Topic not found`
- 根因：该话题是 **FS 话题**（uuid5 形态，评论写 `map/topics/`），不在 DB；而 `create_experiment` 的 topic 校验走 `db.get(Topic, ...)` 只认 DB topic
- 处置：与 M54 一致——话题关联记在 plan frontmatter `dependencies` 文本中，不传 `--topic-id`
- 移交观察：FS 话题与 DB 实验的关联缺口（uuid5 topic 不可被 experiment.topic_id 引用）值得在后续里程碑评估，不在 M55 范围

### 待评审

- 已 submit-for-review，待 reviewer 计划评审（7 条 acceptance / 3 条 evidence_keys / 6 子项范围）
