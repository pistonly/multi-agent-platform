# 执行日志 — 三桥协作干净测试

## 执行环境

- 日期：2026-06-29（UTC）
- MAP API：http://localhost:8001（health 200）
- Web：http://localhost:3000（health 200）
- cursor-sdk：0.1.8
- 三桥 interval：默认 30s
- Host persona：multi-agents-platform-host（8ab78cfc-8289-442e-ae63-fa0df4d2cd68）
- Experiment：a501f61a-7004-4fa9-b051-3d9abc446da5（phase=done，plan v2，review_count=1，log_count=1）
- Git checkpoint before：4f67be8c02a6ef5467510d07de33b42f74cb98bd
- Bridge 请求：execute_experiment，dry_run=false

## 历史证据（host/participant 轮次）

| 项 | 期望 | 实测 |
|----|------|------|
| 话题 discussion_round | ready | ready |
| round_summary_count | 2 | 2 |
| comment_count | ≥1 非 host | 7 |
| host last_posted_summary_round | 2 | 2 |
| host last_handled_comment_id | 61334891-… | 61334891-69d7-40ee-ab01-310f83951600 |
| participant last_handled_trigger_id | af9b9728-… | af9b9728-b4e5-482b-9bff-50255178c246 |
| 本实验 experiment_count | 1 | 1 |
| reviewer last_handled_trigger_id | {exp}:v1 | a501f61a-7004-4fa9-b051-3d9abc446da5:v1 |

来源话题 comment ID 集合（7 条）：

`61334891-69d7-40ee-ab01-310f83951600`, `2ebbe3d0-0319-45ad-b455-aee39c0555bc`, `1c136dd7-cf64-48ee-8228-f01aef86778f`, `acf03ede-8a37-4f55-ba7c-46379b041a03`, `46fff127-46bc-4f02-a3b8-33376a654594`, `af9b9728-b4e5-482b-9bff-50255178c246`, `cd775ae6-1608-4598-9239-fc7be89ff6ac`

## 关键命令

```bash
map --persona host persona whoami
map --persona host topic show --id 69d77715-b2c1-4f85-8d72-188aa452e47b
map --persona host experiment status --id a501f61a-7004-4fa9-b051-3d9abc446da5

# Dry-run 三桥
./scripts/start-host-bridge.sh --once --dry-run 2>&1 | tee /tmp/host-dry-run.log
./scripts/start-participant-bridge.sh --once --dry-run 2>&1 | tee /tmp/participant-dry-run.log
./scripts/start-reviewer-bridge.sh --once --dry-run 2>&1 | tee /tmp/reviewer-dry-run.log

# 真实 cycle + 重启幂等
./scripts/start-all-bridges.sh   # ≥3 interval（~90s）
./tmp/run-bridge-idempotency-test.sh   # cycle-1 + restart-cycle，各 95s

sha256sum .map/*-bridge-state.json
map --persona host experiment review list --id a501f61a-7004-4fa9-b051-3d9abc446da5
```

## Dry-run vs 真实 cycle

### 步骤 A（dry-run）

**Host**（exit 0）：
```
{"action": "promote_experiment", "dry_run": true, "status": "skip_seen", "topic_id": "69d77715-..."}
cycles: 1; dry_run_actions: 0; runner_invocations: 0; runner_skips: 1; runner_errors: 0
```

**Participant**（exit 0）：
```
cycles: 1; comments_created: 0; dry_run_actions: 0; opportunities_seen: 0
```

**Reviewer**（exit 0）：
```
cycles: 1; reviews_created: 0; pending_seen: 0; dry_run_actions: 0
```

### 步骤 B（基线 sha256）

| 文件 | sha256 |
|------|--------|
| host-bridge-state.json | ed9f48844db72fe91b9bdb62fbb14507ec9670c87ecc1a66cc2e7382a4e61f02 |
| participant-bridge-state.json | 09b9872bbd52a0dff4018f32f549eae76e3b75010e508a392d02ff268cc3e88b |
| reviewer-bridge-state.json | a2d693e2a49a19c0dac51a7214da813b2f2e55bd024ac52b3805866c65deab3c |

MAP 基线：topic comment 7 条；experiment review 1 份（11 items）。

dry-run 后三份 state sha256 **全部不变**。

### 步骤 C（真实 cycle）

`./scripts/start-all-bridges.sh` 运行 ≥3 interval；host 日志多次输出：

```
{"action": "promote_experiment", "dry_run": false, "status": "skip_seen", ...}
```

无 `[dry-run]` 前缀；无新 experiment / Round Summary / comment / review 创建。participant/reviewer 在 ready+done 状态下无 pending work。

| 维度 | dry-run 基线 | 真实 cycle 结束 |
|------|-------------|----------------|
| topic comment 数 | 7 | 7（零增量） |
| experiment review 数 | 1 | 1 |
| state sha256 | 见上表 | **完全相同** |

## 重启幂等

- BEFORE：`/tmp/bridge-idempotency-before.json`（2026-06-29T15:26:47Z）
- AFTER：`/tmp/bridge-idempotency-after.json`
- 观察：start-all-bridges ~92s + `run-bridge-idempotency-test.sh`（cycle-1 95s + restart-cycle 95s，各 ≥3 interval）

| 检查项 | BEFORE | AFTER |
|--------|--------|-------|
| topic comment_count | 7 | 7 |
| experiment_count | 1 | 1 |
| review_count | 1 | 1 |
| review_id | 0571a87b-9359-403c-b2da-410fac3b5b58 | 相同 |
| host last_posted_summary_round | 2 | 2 |
| host last_handled_comment_id | 61334891-… | 不变 |
| participant last_handled_trigger_id | af9b9728-… | 不变 |
| reviewer last_handled_trigger_id | a501f61a-…:v1 | 不变 |
| state sha256 | ed9f4884… / 09b9872b… / a2d693e2… | **不变** |

**零增量脏写：通过**（无新 experiment、无重复 Round Summary/review/comment）。

## Reviewer 评审

- review_id：0571a87b-9359-403c-b2da-410fac3b5b58（plan v1）
- items：6 reasonable + 5 unreasonable（均已 resolved；reviewer state 含 5 个 resolved_items）
- plan 已 revise 至 v2（open_unreasonable_count=0）
- 本 run reviewer pending_seen=0，无重复提交

## 结论

| 验收项 | 结果 |
|--------|------|
| 三桥日志可 tail，无未捕获栈 | ✅ |
| host promote 历史证据 + 本 run 无第二 experiment | ✅ skip_seen |
| participant 测试窗口 comment 无重复 | ✅ 零增量 |
| reviewer ≥1 份评审含 reasonable/unreasonable | ✅ |
| dry-run state/MAP 不变 | ✅ |
| 重启后 N≥3 周期零脏写 | ✅ |
| execution log 已写 | ✅ 本文件 |

**总评：三桥干净测试 reviewer + dry-run + 重启幂等验收通过。**

## 风险与后续

- 多实例 lease 未实现 — 留 v0.8
- 实验 phase 已为 done（前序 bridge 已完成 revise→approve→start→execute）；本 run 验收 reviewer/幂等，未再触发 complete
- 辅助脚本 `tmp/run-bridge-idempotency-test.sh` 可复用于后续 bridge 回归

## 文件变更

- 新增：`tmp/run-bridge-idempotency-test.sh`（bridge 幂等回归辅助脚本）
- 更新：`tmp/experiment-a501f61a-log.md`（本执行日志）
- 追加：`.map/bridge-logs/idempotency-test.log`（cycle 启动记录）
- 业务代码：**无修改**；三份 bridge state sha256 全程不变
