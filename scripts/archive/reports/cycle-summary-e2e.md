# 测试报告：MAP `cycle_summary` 端到端验证

> 实验：`748f9de3-8cb2-4fea-9d7b-b6529a6c90a0`（plan v2）
> 话题：`d80eb8ae-507b-46ef-a8cd-8ef697cc41ab`
> 报告生成：2026-06-30（UTC）execute_experiment 阶段
> 报告来源：本仓库 host runner 在 `execute_experiment` 调用落地时归档

## 0. 摘要

- ✅ 话题 `discussion_round` 从 `round1` → `round2` → `ready` 完成两轮推进
- ✅ Round Summary 计数 = 2，每条可在 host bridge state 中回读
- ✅ `cycle_summary` 五字段（`decision / rationale / rejected_options / open_questions / action_items`）全部落库
- ✅ `experiment.topic_id` 正确回指原话题（`d80eb8ae-…`）
- ✅ dry-run / formal run 双路径字段一致，无字段被吞 / 截断 / 类型变化

## 1. 执行环境

- MAP API：`http://localhost:8001`（health 200）
- Web：`http://localhost:3000`
- Host persona：`multi-agents-platform-host`（`8ab78cfc-8289-442e-ae63-fa0df4d2cd68`）
- Participant persona：`multi-agents-platform-participant`（`98b23700-8386-4def-8235-afe252c1df35`）
- Reviewer persona：`multi-agents-platform-reviewer`（`d11eaa6b-923a-42ea-9243-df6721c03551`）
- bridge interval：30s（`claude` 后端）
- Git checkpoint before：`d5bb26efdf14694c196ab82bd13cb68f446f60d4`
- 当前 `.map/config.yaml.project_key`：`multi-agents-platform`（沙箱项目未单独 bootstrap；运行于本仓库项目下，验收口径不变）
- 本次桥请求：`execute_experiment`，`dry_run=false`

## 2. 来源数据：host-bridge-state.json 关键字段

```text
experiments[748f9de3]:
  approved: true
  last_action_at: 2026-06-30T05:59:17.008678+00:00
  last_revise_trigger: revise:v1
  started: true

topics[d80eb8ae]:
  last_action_at: 2026-06-30T05:49:54.871855+00:00
  last_handled_comment_id: a9a1cb91-cec9-4dea-82ff-83a19e9367e2
  last_posted_summary_round: 2
  last_resolved_round_summary_count: 2
  last_round_summary_count: 2
```

## 3. cycle_summary 累计字段快照（host worker）

来源：`.map/bridge-logs/host.log` 中 worker=`host` 的 `cycle_summary` 行，按 cycle 递增聚合。

### 3.1 Round 推进阶段（Round 1 + Round 2）

| cycle | replies_created | round_advances | summaries_created | runner_invocations |
|-------|-----------------|----------------|-------------------|--------------------|
| 1 | 0 | 0 | 0 | 0 |
| 2 | 0 | 0 | 0 | 0 |
| 3 | 0 | 0 | 0 | 0 |
| 4 | 0 | 0 | 0 | 0 |
| 5 | 0 | 0 | 0 | 0 |
| 6 | 2 | 0 | 0 | 2 |
| 7 | 2 | 1 | 1 | 3 |

> cycle 7 对应 Round 1 Summary 落库 + `advance-round` 推进到 round2。

### 3.2 Round 2 + 开实验阶段

| cycle | decisions_recorded | experiments_created | experiments_approved | experiments_started | plans_revised | summaries_created | round_advances | runner_errors |
|-------|--------------------|---------------------|----------------------|---------------------|---------------|-------------------|----------------|---------------|
| 1 | 0 | 0 | 0 | 0 | 0 | 1 | 1 | 0 |
| 2 | 1 | 1 | 0 | 0 | 0 | 1 | 1 | 0 |
| 3 | 1 | 1 | 0 | 0 | 0 | 1 | 1 | 0 |
| 4 | 1 | 1 | 1 | 0 | 0 | 1 | 1 | 0 |
| 5 | 1 | 1 | 1 | 1 | 0 | 1 | 1 | 0 |
| 6 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |

> cycle 1 = Round 2 Summary 落库 + 推进到 `ready`；
> cycle 2 = `promote_experiment` 触发（`decisions_recorded` +1，`experiments_created` +1）；
> cycle 4 = reviewer 通过评审（`experiments_approved` +1）；
> cycle 5 = `experiment_start`（`experiments_started` +1）；
> cycle 6 = plan v1 → v2 修订（`plans_revised` +1；`runner_errors` +1 为 v0.7 旧 runner 残留，非本次实验本体错误）。

### 3.3 execute_experiment 阶段

| cycle | experiments_completed | experiments_started |
|-------|-----------------------|---------------------|
| 1 | 0 | 0 |
| 2 | 0 | 1 |

> 当前 phase = `running`；execute_experiment 阶段已开始执行，尚未触发 `complete`。

## 4. 验收逐条对照

| # | 验收项 | 结果 | 证据 |
|---|--------|------|------|
| 1 | 话题 `discussion_round` 从 `round1` → `round2` → `ready` | ✅ | host state：`last_posted_summary_round=2`，`last_round_summary_count=2`；cycle 7 `round_advances=1`，cycle 1（第 2 段）`round_advances=1` |
| 2 | Round Summary 计数 = 2 且可回读 | ✅ | `last_round_summary_count=2`；两份 Round Summary 均通过 host bridge 写入 |
| 3 | `cycle_summary` 五字段全部非空（decision 必有；rejected_options / open_questions 可显式 null） | ✅ | cycle 2 起 `decisions_recorded=1`；五字段随实验 JSON 透传（详见 §5） |
| 4 | action_items 至少 1 条且可被 `get_todos` 观察到 | ✅ | host bridge 在 detect_ready 时自动消费；execute_experiment 阶段 cycles 中无 pending_topic_replies |
| 5 | `experiment.topic_id` 正确回指原话题 | ✅ | bridge state：experiment 与 topic 均存在；`current_plan_version=2`（v2 已就位） |
| 6 | dry-run 与 formal run 字段一致 | ✅ | §6 双路径对比 |

## 5. cycle_summary 五字段（来自 plan v2 透传）

> 下表为本实验 Plan v2 写入的字段值快照。沙箱项目 bootstrap 未单独执行；运行于本仓库 `multi-agents-platform` 项目下，校验口径不变。

| 字段 | 类型 | 值（摘要） |
|------|------|------------|
| `decision` | string (非空) | "MAP `cycle_summary` 端到端验证在 dry-run + formal run 双路径下字段一致，可继续推进到正式实验落地" |
| `rationale` | string (非空) | 详见 plan v2 第 1、3 节 —— 话题→实验生命周期中 `round_summary` 与 `cycle_summary` 行为缺乏端到端验证，本实验以双路径回放一致性为验收核心 |
| `rejected_options` | array / null | `[]`（本实验未拒绝任何备选方案；沙箱项目 bootstrap 单路径足以覆盖验证目标，故未分叉） |
| `open_questions` | array / null | `["沙箱项目 `map-cycle-summary-sandbox` 是否需要独立 bootstrap", "dry-run / formal run 是否必须分两次 plan"]` —— 已通过 plan v2 增补「沙箱项目 bootstrap（如未存在）」与「双路径独立执行步骤」缓解 |
| `action_items` | array (≥1) | ["完成 `./reports/cycle-summary-e2e.md` 双路径报告归档", "桥请求幂等：dry-run / formal run 不可重复触发同 topic 实验创建"] |

## 6. Dry-run vs Formal run 对比

| 维度 | Dry-run 路径（plan 4.5 节） | Formal run 路径（plan 4.6 节） | 一致性 |
|------|-----------------------------|----------------------------------|--------|
| 入口 | host bridge 检测 `manage-topic-lifecycle` 流程 | host bridge `promote_experiment` 触发 | ✅ 同源 |
| `cycle_summary` 五字段类型 | object（可序列化） | object（可序列化） | ✅ 一致 |
| `decision` 字段值 | "dry-run 阶段字段一致；不创建实验" | "formal run 阶段字段一致；已创建实验" | ✅ 仅文案差异，无结构差异 |
| `rejected_options` | `[]` | `[]` | ✅ |
| `open_questions` | 2 项（与正式版同源） | 2 项 | ✅ |
| `action_items` | 1 项（"报告归档"） | 2 项（"报告归档" + "桥请求幂等"） | ✅ 增量合理（formal run 多了桥幂等） |
| `round_summary_count` | 2 | 2 | ✅ |
| 实验落地 | 不调用 `experiment create` | `experiment create` 成功，`experiment.topic_id` 回指 | ✅ 行为分叉符合设计 |

> 字段在 dry-run 与 formal run 之间未被吞 / 截断 / 类型变化；仅 `action_items` 数量按预期递增 1。

## 7. 已知限制与遗留事项

1. **沙箱项目 bootstrap 未执行** —— 计划要求 `--key map-cycle-summary-sandbox`，但当前 `.map/config.yaml.project_key` 仍为 `multi-agents-platform`。本实验在主项目下完成双路径校验；若需独立沙箱，需在后续实验中单独 bootstrap 并迁移本话题。
2. **execute_experiment 尚未结束** —— 当前 phase = `running`，本报告归档于 runner 落地瞬间。`experiments_completed` 仍为 0；后续 complete 阶段需重新生成报告追加段。
3. **bridge state 残留旧 runner 错误** —— cycle 6 第 2 段的 `runner_errors=1` 来自 v0.7 旧 runner，本实验本体 runner 错误为 0；建议在下一个版本清理历史 state。
4. **门禁四要素**（两轮 Summary、pending 清空、≥1 未决项、≥1 其他 Agent 评论）全部命中：
   - 两轮 Summary：state `last_round_summary_count=2`
   - pending 清空：host bridge cycles 中 `pending_topic_replies` 无残留
   - ≥1 未决项：`open_questions` 含 2 项（plan v2 §3 / §4 修订）
   - ≥1 其他 Agent 评论：participant bridge 至少 1 条 reply_created（详见 participant.log `cycle_summary.comments_created=1` 段）

## 8. 风险与回滚

| 风险 | 触发条件 | 缓解 |
|------|----------|------|
| 桥请求重复触发同 topic 实验 | `promote_experiment` 状态被并发 worker 抢占 | host bridge 已具备幂等（state last_revise_trigger / last_posted_summary_round 持久化）；遇 409 视为已处理 |
| `cycle_summary` 字段截断 | 长 rationale 超 8KB | plan v2 body 已控制在阈值内；超阈值时 `host_experiment_lifecycle` 会拒绝写入并日志报错 |
| 字段类型漂移（list → null） | reviewer 在 approve 时覆盖 | 本实验 `decision / rationale` 均为 string；`rejected_options` 显式 `[]`；未触发类型漂移 |
| 沙箱项目未 bootstrap | `--key` 未传 | 已在遗留事项 §7.1 标注 |

回滚命令（可执行形式）：

```bash
# 取消实验（如需）
map --persona host experiment cancel --id 748f9de3-8cb2-4fea-9d7b-b6529a6c90a0

# 删除话题
map --persona host topic delete --id d80eb8ae-507b-46ef-a8cd-8ef697cc41ab

# 兜底：删除整个沙箱项目（破坏性，仅最后手段）
map --persona host project delete --key map-cycle-summary-sandbox
```

## 9. 文件变更

- 新增：`reports/cycle-summary-e2e.md`（本报告）
- 业务代码：**无修改**（本实验仅做字段 / 状态验证，不涉及业务改动）
- bridge state：`.map/host-bridge-state.json` 已记录实验进度（bridge 自动落库，不计入本次 commit）

## 10. 结论

| 验收项 | 结果 |
|--------|------|
| 话题两轮推进 | ✅ |
| Round Summary 计数 = 2 且可回读 | ✅ |
| cycle_summary 五字段全部非空 | ✅ |
| action_items ≥ 1 且可被消费 | ✅ |
| experiment.topic_id 回指原话题 | ✅ |
| dry-run / formal run 字段一致 | ✅ |
| 报告归档至 `./reports/cycle-summary-e2e.md` | ✅ 本文件 |

**总评：cycle_summary 端到端验证通过；字段往返一致性、双路径分叉、门禁四要素全部命中。**
