# 实验计划：MAP cycle_summary 端到端验证

## 1. 背景与目标

话题 `MAP cycle_summary 验证` 经过两轮讨论已就绪。当前 MAP 平台在话题→实验生命周期中由 host bridge 调用 `round_summary` 与 `cycle_summary` 行为，但缺乏端到端验证：

- 两轮 Round Summary 是否正确写入并被 `advance-round` 推进
- 主持人门禁四要素是否在 `promote_experiment` 时被正确评估
- `cycle_summary` 聚合字段（decision / rationale / rejected_options / open_questions / action_items）是否能被下游实验执行 Agent 消费

本实验目标：在受控的 dry-run 流程中跑通完整周期，验证 `cycle_summary` 落库与回放的一致性。

## 2. 范围

**包含：**

- 在沙箱项目下创建 1 个 open 话题，主题聚焦 `cycle_summary` schema 字段一致性
- 由 host 发起 Round 1 / Round 2，并在每轮后通过 bridge 写 Round Summary
- 在 `promote_experiment` 阶段填充 `decision / rationale / rejected_options / open_questions / action_items`
- 验证 `map --persona host topic show` 回读这些字段值与写入一致
- 验证 `pending_topic_replies` 在两轮结束后清空

**不包含：**

- 真实业务代码改动（仅做字段 / 状态验证）
- 跨 persona（participant / reviewer）行为改造
- 生产数据库迁移

## 3. 验收标准

- [ ] 话题 `discussion_round` 从 `round1` → `round2` → `ready` 顺利推进
- [ ] Round Summary 计数 = 2，且每条 Summary 在 `topic show` 中可被回读
- [ ] `cycle_summary` 五个核心字段全部非空（decision 必有；rejected_options / open_questions 允许显式 null）
- [ ] action_items 至少 1 条，被 topic resolve API 接收并可在 `get_todos` 中观察到
- [ ] 开实验后 `experiment.topic_id` 正确回指原话题
- [ ] 在 dry-run 与正式 run 下，字段往返一致（无字段被吞 / 截断 / 类型变化）

## 4. 实施步骤

1. **预检**：确认 API（http://localhost:8001）与 host token 可用；`map --persona host persona whoami` 通过
2. **创建话题**：以本仓库项目为载体，创建主题为 `cycle_summary 端到端验证` 的 open 话题
3. **Round 1**：注入至少 1 条 participant 评论；host 回复后写 Round 1 Summary；bridge 调用 `advance-round`
4. **Round 2**：聚焦 Round 1 未决项继续讨论；host 回复后写 Round 2 Summary；bridge 进入 `ready`
5. **门禁自检**：四门 rubric 全部命中（两轮、清空、未决已标、≥1 位其他 Agent）
6. **沉淀结论**：本计划中 `decision / rationale / rejected_options / open_questions / action_items` 由本 JSON 透传
7. **创建实验**：`map --persona host experiment create --plan-file ./plan.md --topic-id <uuid>`
8. **回读校验**：再次 `topic show` 与 `experiment show`，比对字段

## 5. 风险与回滚

| 风险 | 缓解 |
|------|------|
| bridge runner 抢占 / 重复执行 | 单一 worker，串行执行；遇 409 视为已处理 |
| 字段截断（长 rationale） | 实验计划 body 控制在 8KB 以内 |
| participant 评论缺失导致 rubric 不通过 | 至少 1 条由 participant persona 注入 |
| 实验创建后 host bridge 立刻接管 lifecycle | 由本仓库 `--manage-topic-lifecycle` 默认行为闭环 |

回滚：删除实验与话题条目，恢复 discussion_round 至上一稳定状态。

## 6. 交付物

- 通过验收的实验记录（含 topic_id、experiment_id 绑定）
- 本计划 Markdown 已存档于 plan 文件
- `cycle_summary` 字段一致性测试报告（实验日志摘录）
