# 三桥协作干净测试

## 来源话题

- topic_id: `69d77715-b2c1-4f85-8d72-188aa452e47b`
- title: 三桥协作干净测试
- discussion_round: `ready`
- round_summary_count: `2`
- active_experiment_id: `a501f61a-7004-4fa9-b051-3d9abc446da5`（本实验）

## 背景

v0.7 P2 已落地 host / participant / reviewer 三条 bridge 与 Cursor SDK runner 接线。来源话题经两轮讨论已收敛并 **promote 为本实验**；host state 已有 `last_posted_summary_round: 2`。本实验在 **review 阶段** 重点验证：reviewer bridge 消费评审、三桥 `--dry-run` vs 真实 cycle 差异、以及 **重启后零增量脏写** 的幂等性。

## 目标

1. 三桥（host / participant / reviewer）同时常驻 polling，在本实验窗口内完成 **reviewer + 幂等** 协作闭环。
2. **（执行前已满足）** host bridge 生命周期：reply_pending → Round Summary ×2 → promote_experiment — 以来源话题与 `.map/host-bridge-state.json` 为历史证据，本 run **不再重复 promote**。
3. **（执行前已满足）** participant 在来源话题 Round 1/2 的评论与 host 回复 — 以 MAP 话题快照为历史证据；本 run 仅验收 **测试窗口内无重复 comment**。
4. 验证 reviewer bridge 对本实验提交/更新评审（reasonable / unreasonable），且 revise_plan 等 lifecycle 动作幂等。
5. 确认 `.map/*-bridge-state.json` 在 **重启后 N 个 polling 周期内** 不产生 MAP 侧增量脏写。

## 范围

### 必做

- 使用 `./scripts/start-all-bridges.sh` 启动三桥；日志目录 `.map/bridge-logs/{host,participant,reviewer}.log`。
- host bridge：`scripts/cursor-host-runner.py`，state `.map/host-bridge-state.json`。
- participant bridge：`scripts/cursor-participant-runner.py`，state `.map/participant-bridge-state.json`。
- reviewer bridge：`scripts/cursor-reviewer-runner.py`，state `.map/reviewer-bridge-state.json`。
- 本实验（`a501f61a-7004-4fa9-b051-3d9abc446da5`）已由 host promote 并处于 review；reviewer bridge 应已处理 plan v1（state 键 `experiments.<id>.last_handled_trigger_id`）。
- **先 dry-run 后真实 cycle**（见「Dry-run 对比 procedure」）。
- **重启幂等 procedure**（见「重启幂等验收 procedure」）。

### 不做

- 不在 MAP API / Web 内嵌 Cursor / OpenAI / Anthropic SDK。
- 不自动 approve / start / complete 实验（保持 human-in-the-loop）。
- 不为复测 host promote / Round 1/2 另建 disposable 话题（历史证据 + 本 run 幂等已覆盖）；多实例 lease 留待 v0.8。

## 执行前历史证据（host / participant 轮次）

以下在 **本实验执行前已满足**，执行者记录快照即可，**不要求本 run 再次 promote 或新发 Round Summary**：

```bash
# 来源话题状态
map --persona host topic show --id 69d77715-b2c1-4f85-8d72-188aa452e47b

# host state（期望 topics.69d77715…last_posted_summary_round == 2）
cat .map/host-bridge-state.json

# 本实验存在且 phase=review
map --persona host experiment status --id a501f61a-7004-4fa9-b051-3d9abc446da5
```

验收：话题 `discussion_round=ready`、无 pending；host state 含 `last_posted_summary_round: 2`；本实验 ID 与 promote 记录一致。host_worker 对「话题已有 active experiment」**不会再 promote** — 本 run 观察 host 仅处理 pending / experiment lifecycle（如 revise_plan），不期望新 experiment。

## Dry-run 对比 procedure

**步骤 A — 各桥单次 dry-run（不写 MAP、不更新 state）：**

```bash
./scripts/start-host-bridge.sh --once --dry-run 2>&1 | tee /tmp/host-dry-run.log
./scripts/start-participant-bridge.sh --once --dry-run 2>&1 | tee /tmp/participant-dry-run.log
./scripts/start-reviewer-bridge.sh --once --dry-run 2>&1 | tee /tmp/reviewer-dry-run.log
```

**步骤 B — 记录 dry-run 基线（MAP 计数与 state checksum）：**

```bash
export EXP_ID=a501f61a-7004-4fa9-b051-3d9abc446da5
export TOPIC_ID=69d77715-b2c1-4f85-8d72-188aa452e47b

# 评论 ID 列表（来源话题）
map --persona host topic show --id $TOPIC_ID | jq '[.. | objects | select(.id? and .body?) | .id]'

# 评审数量
map --persona host experiment review list --id $EXP_ID | jq 'length'

# state 文件 checksum（dry-run 后应不变）
sha256sum .map/host-bridge-state.json .map/participant-bridge-state.json .map/reviewer-bridge-state.json
```

**步骤 C — 真实 cycle：**

```bash
./scripts/start-all-bridges.sh
# 观察 ≥3 个 interval（默认 30s → ≥90s）后 Ctrl+C
```

**对比字段（dry-run vs 真实）：**

| 维度 | dry-run 期望 | 真实 cycle 期望 |
|------|-------------|----------------|
| 日志 | 含 `[dry-run]` 前缀、`would …` 意图 | 无 `[dry-run]`；有 runner 调用与 MAP 写入日志 |
| state 文件 | sha256 **不变** | 仅在有新 work 时更新对应键 |
| MAP comment 计数 | 与步骤 B 相同 | 测试窗口内 **无重复** comment ID |
| MAP review 计数 | 与步骤 B 相同 | 本实验 review 不重复创建（可 revise_plan / resolve items） |
| runner exit code | 0 / 1 / 2 | 同上，无未捕获栈 |

## 重启幂等验收 procedure

**1. 重启前 snapshot（写入 `/tmp/bridge-idempotency-before.json` 或实验 log 附录）：**

```bash
export EXP_ID=a501f61a-7004-4fa9-b051-3d9abc446da5
export TOPIC_ID=69d77715-b2c1-4f85-8d72-188aa452e47b

BEFORE=$(mktemp)
jq -n \
  --arg exp "$EXP_ID" \
  --arg topic "$TOPIC_ID" \
  --slurpfile host .map/host-bridge-state.json \
  --slurpfile part .map/participant-bridge-state.json \
  --slurpfile rev .map/reviewer-bridge-state.json \
  '{
    ts: (now | todate),
    experiment_reviews: null,
    topic_comment_ids: null,
    host_state: $host[0],
    participant_state: $part[0],
    reviewer_state: $rev[0]
  }' > "$BEFORE"

# 填充 MAP 侧计数（需 jq 解析 CLI JSON 输出）
map --persona host experiment review list --id $EXP_ID > /tmp/reviews.json
map --persona host topic show --id $TOPIC_ID > /tmp/topic.json
# 记录：reviews 条数、reviews[].items[].id、topic 下全部 comment id
cp "$BEFORE" /tmp/bridge-idempotency-before.json
```

**2. 停止并重启三桥（保留 state 文件，不删除 `.map/*-bridge-state.json`）：**

```bash
# Ctrl+C 停止 start-all-bridges.sh
./scripts/start-all-bridges.sh
```

**3. 观察 N=3 个 polling 周期**（`MAP_*_INTERVAL` 默认 30s → 等待 ≥90s）。

**4. 重启后对比 — 期望零增量脏写：**

```bash
# MAP：comment id 集合、review id/item id 集合与 BEFORE 相同（允许 revise_plan 产生新 plan version，但不重复 create experiment / 重复 review 条目）
map --persona host experiment review list --id $EXP_ID
map --persona host topic show --id $TOPIC_ID
map --persona host experiment list --topic-id $TOPIC_ID  # 仍仅本实验一条 active

# state 键检查（值应稳定或仅 last_action_at 刷新，trigger/idempotency 键不变）：
# host:     topics.$TOPIC_ID.last_posted_summary_round == 2
# host:     topics.$TOPIC_ID.last_handled_comment_id 不变
# participant: topics.$TOPIC_ID.last_handled_trigger_id 不变
# reviewer: experiments.$EXP_ID.last_handled_trigger_id == "${EXP_ID}:v1"（或当前 plan version 对应 trigger）
jq '.topics["'"$TOPIC_ID"'"]' .map/host-bridge-state.json
jq '.topics["'"$TOPIC_ID"'"]' .map/participant-bridge-state.json
jq '.experiments["'"$EXP_ID"'"]' .map/reviewer-bridge-state.json
```

**失败判定：** 新 experiment_id、重复 Round Summary comment、重复 review 文档、participant 重复 comment（同 trigger 再发）、reviewer 对同一 `last_handled_trigger_id` 再提交。

## 执行步骤

1. **环境**：API `:8001`、Web `:3000` 可用；`.map/` 三 persona token 有效；`pip install cursor-sdk`；`map --persona host persona whoami` 确认身份。
2. **历史证据快照**：执行「执行前历史证据」命令，确认 host/participant 轮次 **已满足**。
3. **Dry-run**：按「Dry-run 对比 procedure」步骤 A–B 执行并保存日志。
4. **真实 cycle**：`./scripts/start-all-bridges.sh`，tail 三桥日志 ≥3 interval；确认 reviewer 对本实验有评审活动（含 reasonable / unreasonable）。
5. **Participant 测试窗口**：对比步骤 B 与步骤 4 结束时的 comment ID 集合 — **增量为 0 或仅为预期 follow_up（无重复 ID）**；不要求 `ready` 话题上再发 Round 1/2。
6. **重启幂等**：按「重启幂等验收 procedure」执行 snapshot → 重启 → N 周期 → 对比。
7. **收尾**：填写 execution log（见模板），`map --persona host experiment complete` 或 `experiment log`（**不**在本步骤自动 approve/start）。

## 验收标准

- [ ] 三桥日志可 `tail -f .map/bridge-logs/*.log`，无未捕获异常栈；runner exit code 仅 0/1/2。
- [ ] **（历史证据）** 来源话题 promote 为本实验；`.map/host-bridge-state.json` 中 `topics.69d77715-….last_posted_summary_round: 2`；本 run **未** 创建第二个 experiment。
- [ ] **（历史证据 + 测试窗口）** participant 在来源话题已有非 host 评论；本 run 测试窗口内 **comment ID 无重复增量**。
- [ ] reviewer 对本实验至少有 1 份评审（含 reasonable 与/或 unreasonable）；revise_plan 后 state/log 可追踪。
- [ ] dry-run：日志含 `[dry-run]`，state sha256 与 MAP 计数相对步骤 B **不变**。
- [ ] 重启后 N=3 周期内：**不**重复 experiment / Round Summary / review / comment（见重启 procedure 失败判定）。
- [ ] execution log 已提交且含模板所列章节（环境、命令、结论、风险、后续）。

## Execution log 模板

执行完成后写入 `./tmp/experiment-a501f61a-log.md`，再：

```bash
map --persona host experiment log \
  --id a501f61a-7004-4fa9-b051-3d9abc446da5 \
  --summary "三桥干净测试：reviewer+幂等通过" \
  --file ./tmp/experiment-a501f61a-log.md
```

**Markdown 骨架：**

```markdown
# 执行日志 — 三桥协作干净测试

## 执行环境
- 日期 / MAP API / Web / cursor-sdk 版本 / 三桥 interval

## 历史证据（host/participant 轮次）
- topic show 摘要、host/participant state 摘录

## 关键命令
- dry-run 三命令、start-all-bridges、重启 snapshot 命令

## Dry-run vs 真实 cycle
- [dry-run] 日志样例、state sha256 对比、MAP 计数对比

## 重启幂等
- BEFORE/AFTER comment id 集合、review 计数、state 键对比、观察周期数

## 结论
- 通过 / 未通过项对照验收 checkbox

## 风险与后续
- 多实例 lease、凭证失败等；v0.8 动作
```

## 风险与回滚

- **CURSOR_API_KEY / 模型不可用**：runner exit 1，bridge 不写 MAP；修复凭证后重试。
- **host 非 creator_agent_id**：experiment lifecycle 403；须用 host persona。
- **并发多 bridge 实例**：可能重复写入；本实验仅单实例，文档注明限制。
- **ready 话题无新 Round 评论**：预期行为；participant 仅 follow_up，以测试窗口零重复为准。

## 讨论共识摘要

- MAP 管状态与权限；Agent Runtime 管推理；bridge 管 polling、幂等与 persona 写入。
- 来源话题 host/participant 轮次 **执行前已满足**；本实验验收重点为 **reviewer + dry-run 对比 + 重启幂等**。
- 未决项「多实例 lease」带入后续版本，不阻塞本实验。
