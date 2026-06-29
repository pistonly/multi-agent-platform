# 执行日志 — 三桥协作干净测试

## 执行环境

- 日期：2026-06-29（UTC+8）
- MAP API：`http://localhost:8001`（health 200）
- Web：`http://localhost:3000`（200）
- cursor-sdk：0.1.8
- 三桥 interval：默认 30s
- Host persona：`multi-agents-platform-host`（`map --persona host persona whoami` 通过）
- Bridge 请求：`execute_experiment`，`dry_run: true`，experiment `a501f61a-7004-4fa9-b051-3d9abc446da5`

## 历史证据（host/participant 轮次）

| 项 | 期望 | 实测 |
|----|------|------|
| 话题 `discussion_round` | `ready` | `ready` |
| `round_summary_count` | 2 | 2 |
| `comment_count` | ≥7 | 7 |
| `experiment_count` | 1 | 1（仅本实验） |
| host state `last_posted_summary_round` | 2 | 2 |
| host state `last_handled_comment_id` | 61334891-… | 61334891-69d7-40ee-ab01-310f83951600 |
| participant state `last_handled_trigger_id` | af9b9728-… | af9b9728-b4e5-482b-9bff-50255178c246 |
| 实验 phase | running | running（plan v2，review_count=1，open_unreasonable=0） |
| reviewer state `last_handled_trigger_id` | `{exp}:v1` | a501f61a-7004-4fa9-b051-3d9abc446da5:v1 |

来源话题 comment ID 集合（7 条）：

`61334891-69d7-40ee-ab01-310f83951600`, `2ebbe3d0-0319-45ad-b455-aee39c0555bc`, `1c136dd7-cf64-48ee-8228-f01aef86778f`, `acf03ede-8a37-4f55-ba7c-46379b041a03`, `46fff127-46bc-4f02-a3b8-33376a654594`, `af9b9728-b4e5-482b-9bff-50255178c246`, `cd775ae6-1608-4598-9239-fc7be89ff6ac`

## 关键命令

```bash
map --persona host persona whoami
map --persona host topic show --id 69d77715-b2c1-4f85-8d72-188aa452e47b
map --persona host experiment status --id a501f61a-7004-4fa9-b051-3d9abc446da5
./scripts/start-host-bridge.sh --once --dry-run
./scripts/start-participant-bridge.sh --once --dry-run
./scripts/start-reviewer-bridge.sh --once --dry-run
sha256sum .map/*-bridge-state.json
```

## Dry-run vs 真实 cycle

### 步骤 A（dry-run）

- **host**：`./scripts/start-host-bridge.sh --once --dry-run` → bridge exit 0；`cycles=1`，`runner_invocations=1`，`dry_run_actions=1`，`runner_errors=0`。日志含 `[dry-run] would complete experiment=a501f61a-7004-4fa9-b051-3d9abc446da5`（running 实验触发 execute_experiment 路径；真实 cursor-host-runner ~135s，未写 MAP/state）。本 bridge 响应返回 JSON 执行日志。
- **participant**：exit 0；`cycles=1`，`opportunities_seen=0`，`comments_created=0`，`dry_run_actions=0`（ready 话题无新机会）。
- **reviewer**：exit 0；`pending_seen=0`，`reviews_created=0`，`dry_run_actions=0`（plan v1 trigger 已在 state，5 unreasonable items 已 resolved）。

### 步骤 B（基线）

| 指标 | 值 |
|------|-----|
| topic comment 数 | 7 |
| experiment review 数 | 1 |
| review item 数 | 11（6 reasonable + 5 unreasonable，均已 resolved） |
| host state sha256 | `ed9f48844db72fe91b9bdb62fbb14507ec9670c87ecc1a66cc2e7382a4e61f02` |
| participant state sha256 | `09b9872bbd52a0dff4018f32f549eae76e3b75010e508a392d02ff268cc3e88b` |
| reviewer state sha256 | `a2d693e2a49a19c0dac51a7214da813b2f2e55bd024ac52b3805866c65deab3c` |

dry-run 后三份 state sha256 **全部不变**；MAP comment/review 计数无增量。

### 步骤 C（真实 cycle）

**本 bridge 调用为 `dry_run: true`，未启动 `./scripts/start-all-bridges.sh` 真实 cycle。** 真实 cycle 与 MAP 写入留待非 dry-run 执行轮次。

## 重启幂等

已写入 `/tmp/bridge-idempotency-before.json`（三份 state + comment/review 计数）。**本 dry-run 轮次未执行**重启 → N 周期对比 procedure。历史 state 键已满足幂等前置：

- host：`last_posted_summary_round=2`，`last_handled_comment_id` 稳定，`experiments.a501f61a.approved=true`，`started=true`
- participant：`last_handled_trigger_id=af9b9728-…` 稳定
- reviewer：`last_handled_trigger_id=a501f61a-…:v1`，5 个 resolved_items 已记录

## 结论（对照验收 checkbox）

| 验收项 | 状态 |
|--------|------|
| 历史证据：promote + Round Summary ×2 | ✅ 已满足（执行前） |
| 未创建第二个 experiment | ✅ |
| participant 历史评论 + 本窗口零重复 | ✅ dry-run 窗口 0 增量 |
| reviewer ≥1 份评审（含 reasonable/unreasonable） | ✅ 1 份，11 items，5 unreasonable 已 resolved → plan v2 |
| dry-run：participant/reviewer state sha256 不变 | ✅ |
| dry-run：host execute_experiment JSON 回写 | ✅（本 run） |
| 三桥真实 cycle ≥3 interval | ⏳ 待非 dry-run 执行 |
| 重启后 N 周期零脏写 | ⏳ 待非 dry-run 执行 |
| execution log 提交 MAP | ⏳ 本 run 为 dry-run，bridge 不写 MAP |

## 风险与后续

- **runner_timeout**：host bridge 默认超时；running 实验 dry-run 递归调用 execute_experiment 可能触发 `runner_errors=1`，可增大 `MAP_HOST_RUNNER_TIMEOUT` 或独立验收 participant/reviewer dry-run。
- **host dry-run 与 running experiment**：使用真实 runner 时 host dry-run 会递归调用 execute_experiment；独立验收 participant/reviewer dry-run 更直接。
- **多实例 lease**：仍留 v0.8；本实验单实例假设不变。
- **真实 cycle**：需 `./scripts/start-all-bridges.sh` ≥90s 观察 + `/tmp/bridge-idempotency-before.json` 重启对比。
- **complete**：human-in-the-loop，非 dry-run 时由 bridge 调用 `map experiment log` / `complete`。

## 文件变更

- 更新：`tmp/experiment-a501f61a-log.md`（本日志）
- 写入：`/tmp/bridge-idempotency-before.json`（幂等基线快照）
- 仓库代码：**无**（本实验为验收型，dry-run 轮次无需改代码）
