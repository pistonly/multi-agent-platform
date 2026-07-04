# 实验计划：MAP cycle_summary 端到端验证（修订版 v2）

## 1. 背景与目标

话题 `MAP cycle_summary 验证` 经过两轮讨论已就绪。当前 MAP 平台在话题→实验生命周期中由 host bridge 调用 `round_summary` 与 `cycle_summary` 行为，但缺乏端到端验证：

- 两轮 Round Summary 是否正确写入并被 `advance-round` 推进
- 主持人门禁四要素是否在 `promote_experiment` 时被正确评估
- `cycle_summary` 聚合字段（decision / rationale / rejected_options / open_questions / action_items）是否能被下游实验执行 Agent 消费

本实验目标：在受控的 dry-run + formal run 双流程中跑通完整周期，验证 `cycle_summary` 落库与回放的一致性。

## 2. 范围

**包含：**

- 在**沙箱项目** `map-cycle-summary-sandbox`（`--key map-cycle-summary-sandbox`）下创建 1 个 open 话题，主题聚焦 `cycle_summary` schema 字段一致性
- 由 host 发起 Round 1 / Round 2，并在每轮后通过 bridge 写 Round Summary
- 在 `promote_experiment` 阶段填充 `decision / rationale / rejected_options / open_questions / action_items`
- 验证 `map --persona host topic show` 回读这些字段值与写入一致
- 验证 `pending_topic_replies` 在两轮结束后清空
- 分别执行一次 **dry-run**（仅 schema 字段往返，不创建正式实验）和一次 **formal run**（落地真实实验条目）

**不包含：**

- 真实业务代码改动（仅做字段 / 状态验证）
- 跨 persona（participant / reviewer）行为改造
- 生产数据库迁移

## 3. 验收标准

- [ ] 话题 `discussion_round` 从 `round1` → `round2` → `ready` 顺利推进（dry-run + formal run 各一次）
- [ ] Round Summary 计数 = 2，且每条 Summary 在 `topic show` 中可被回读
- [ ] `cycle_summary` 五个核心字段全部非空（decision 必有；rejected_options / open_questions 允许显式 null）
- [ ] action_items 至少 1 条，被 topic resolve API 接收并可在 `get_todos` 中观察到
- [ ] 开实验后 `experiment.topic_id` 正确回指原话题
- [ ] dry-run 与 formal run 双路径下字段往返一致（无字段被吞 / 截断 / 类型变化）
- [ ] 测试报告归档至 `./reports/cycle-summary-e2e.md`，包含两轮 Summary 文本、cycle_summary 五字段、topic_id / experiment_id 回读对照

## 4. 实施步骤

### 4.1 预检与项目准备

1. 确认 API（http://localhost:8001）与 host token 可用；`map --persona host persona whoami` 通过
2. **沙箱项目 bootstrap**（如未存在）：`map --persona host bootstrap --key map-cycle-summary-sandbox --name "cycle_summary sandbox" --api-url http://localhost:8001`
3. 校验 `.map/config.yaml` 中 `project_key == map-cycle-summary-sandbox`
4. 设超时阈值：`--advance-round-timeout 30s`、`--promote-experiment-timeout 30s`、`--overall-deadline 600s`（任一超时立即终止并回滚）

### 4.2 创建话题

5. 在沙箱项目下创建主题为 `cycle_summary 端到端验证` 的 open 话题：`map --persona host topic create --title "cycle_summary 端到端验证" --project-key map-cycle-summary-sandbox`
6. 记录 `topic_id` 至实验日志

### 4.3 Round 1（含 participant 评论注入与未决项预设）

7. **预先注入至少 1 条 participant 评论**（在 `advance-round` 调用前必须落库）：
   ```bash
   map --persona participant topic comment --id <topic_id> \
     --body "建议 cycle_summary 的 rejected_options 字段在无拒绝方案时显式存为 null，而非空数组，便于下游类型判断"
   ```
8. 校验 `pending_topic_replies` 中可见该 participant comment
9. host 撰写 Round 1 回复并写 Round 1 Summary（包含**至少 1 条预设未决项**作为 Round 2 抓手，例如 `open_questions: ["rejected_options 字段在空时应为 null 还是 []？", "action_items 是否需要 owner 字段？"]`）
10. bridge 调用 `advance-round` → `discussion_round` 进入 `round2`

### 4.4 Round 2（聚焦 Round 1 未决项）

11. 注入 Round 2 participant 评论：**显式回应** Round 1 的 open_questions（如「rejected_options 建议为 null；action_items 不增加 owner」）
12. host 撰写 Round 2 Summary（**逐条回答** Round 1 的 open_questions 并落到 rejected_options / decision）
13. bridge 写 Round 2 Summary 后进入 `ready`，`pending_topic_replies` 清空

### 4.5 门禁自检（dry-run 路径）

14. 四门 rubric 全部命中：
    - 两轮 Summary 已落库（计数 = 2）
    - `pending_topic_replies` 清空
    - Round 1 Summary 中 `open_questions` 至少 1 条（已显式标记未决）
    - ≥1 位其他 Agent（participant）已评论
15. dry-run 阶段**不**调用 `experiment create`，仅 `topic show --id <topic_id>` 校验 `cycle_summary` 五字段非空、类型正确
16. 字段往返比对结果写入 `./reports/cycle-summary-e2e.md` 的「Dry-run 验证」小节

### 4.6 正式实验落地（formal run 路径）

17. `map --persona host experiment create --plan-file ./plan.md --topic-id <topic_id>`
18. 校验 `experiment.topic_id == <topic_id>`，`current_plan_version == 2`
19. 再次 `topic show` 与 `experiment show`，比对字段（decision / rationale / rejected_options / open_questions / action_items）
20. formal run 结果写入 `./reports/cycle-summary-e2e.md` 的「Formal run 验证」小节

### 4.7 报告归档

21. 生成报告 `./reports/cycle-summary-e2e.md`，包含：
    - 两轮 Round Summary 原文
    - cycle_summary 五字段快照（含类型）
    - topic_id / experiment_id / plan_version 对照表
    - dry-run 与 formal run 差异分析
    - 已知限制与遗留事项

## 5. 风险与回滚

| 风险 | 缓解 |
|------|------|
| bridge runner 抢占 / 重复执行 | 单一 worker，串行执行；遇 409 视为已处理 |
| 字段截断（长 rationale） | 实验计划 body 控制在 8KB 以内 |
| participant 评论缺失导致 rubric 不通过 | 步骤 7 显式声明「在 advance-round 调用前至少 1 条 participant 评论已落库」 |
| bridge 在 detect_ready 时自动触发 promote_experiment | 已在步骤 14–15 显式列出可验证步骤，不再依赖未文档化的 flag |
| bridge runner 串行模式整体超时 | 步骤 4 设 `--overall-deadline 600s`；任一阶段超时立即终止并进入回滚 |
| dry-run 与 formal run 字段不一致 | 步骤 19 显式做交叉比对，记录差异到报告 |

**回滚命令（可执行形式）：**

```bash
# 删除实验（如已创建）
map --persona host experiment cancel --id <experiment_id>

# 删除沙箱话题
map --persona host topic delete --id <topic_id>

# 若需恢复 discussion_round 至 round1（极端情况）
map --persona host topic reset-round --id <topic_id> --to round1

# 兜底：删除整个沙箱项目（破坏性，仅最后手段）
map --persona host project delete --key map-cycle-summary-sandbox
```

## 6. 交付物

- 通过验收的实验记录（含 topic_id、experiment_id 绑定，`current_plan_version == 2`）
- 本计划 Markdown 已存档于 plan 文件（v2）
- 字段一致性测试报告：`./reports/cycle-summary-e2e.md`（含 dry-run + formal run 双路径对比）
- 沙箱项目 `map-cycle-summary-sandbox` 的 `.map/config.yaml` 快照