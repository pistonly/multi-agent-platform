# 实验生命周期转换参考

> 本文档从 [experiment-host SKILL.md](../SKILL.md) 提取的深度参考。当实验进入不同 phase 需要判断执行什么动作时阅读本文件。

## my_open_experiments 各阶段

| phase | 你应执行的动作 |
|-------|----------------|
| `draft` | `map experiment submit-review --id <id>` |
| `review` 且 `open_unreasonable_count > 0` | 修订 plan（见 [execution-cookbook.md](execution-cookbook.md) §revise_plan） |
| `review` 且 `open_unreasonable_count = 0` | `map experiment approve --id <id>` |
| `approved` | `map experiment start --id <id>` |
| `running` | 按 plan 改代码、跑测试、写 log（见下方 execute_experiment）；plan 全部验收通过后 `map experiment complete` 提交结果待审批 |
| `result_review` | 等 reviewer `accept-result` 或 `reject-result`；若被驳回回到 `running`，继续返工 |

> **观察项（v0.9 M30A+M31）**：`running` 期间如改动涉及 `inbound_events.rejection_count`（v1 fingerprint 拒绝路径），需在实验日志里附监控口径 —— 单 fingerprint `rejection_count` 增长率、累计 top-N fingerprint、是否需要 `map admin notification cleanup-v1` 兜底（待后续实验定义）。

收到 `my_open_experiments` 待办 wake 时：

```bash
map --persona host experiment status --id <id>
```

若 waker prompt 或 `map work` 中的 `my_open_experiments` 样例带有 `actions`，这就是 host 的实验推进义务：先 `experiment status` 核实，再根据 **phase**、`actions` 与 **open_unreasonable_count** 执行上表对应动作。`actions=[]` 且 `blocked_on` 表示等待他人时，记录等待状态即可；`running` 阶段应实际推进执行工作，而不是只检查状态后结束。

## execute_experiment（running 阶段）

0. **（推荐）** `map --persona host experiment lock acquire --id <id>` — 若 lock busy 则 skip 并记录退避时间
1. `map experiment status --id <id>` 阅读 `current_plan`
2. `git status --short` / `git diff --stat` 检查工作树边界；若已有无关 dirty 改动且无法安全拆分，写 blocker 后停止
3. 在仓库内**实际修改**文件；小步、可验证；不要无关重构
4. 运行 plan 中列出的验证命令（pytest、grep 等）
5. 将当前实验改动提交为窄 commit（用户明确禁止提交 git 时除外）
6. 将执行记录写入临时文件，例如 `.map/generated-plans/experiment-<id>-log.md`，内容含：做了什么、改了哪些文件、验证结果、commit_sha、`git_status_after`、风险与后续
7. 写入 MAP：

```bash
map --persona host experiment log \
  --id <exp-uuid> \
  --summary "I1 完成：…" \
  --file ./path/to/log.md
```

8. 若 plan 定义的**全部 acceptance** 已满足，先准备 evidence metadata（例如 `.map/generated-plans/experiment-<id>-evidence.yaml`，包含 `pytest_summary` / `alembic_current` / `api_health` / `image_digest` / `evidence` / `commit_sha` 等至少一项），执行 `map experiment pre-complete --metadata ...`，再调用 `map experiment complete --metadata ...` 提交最终结果日志，实验进入 `result_review`；否则结束本次 wake，等待下次 `experiment_lifecycle` wake 继续下一子项
9. **`experiment lock release --id <id>`**（若步骤 0 已 acquire）

```bash
map --persona host experiment pre-complete \
  --id <exp-uuid> \
  --metadata ./path/to/evidence.yaml

map --persona host experiment complete \
  --id <exp-uuid> \
  --summary "结果提交：…" \
  --file ./path/to/log.md \
  --metadata ./path/to/evidence.yaml
```

## running 收尾模板

```bash
git status --short
git diff --stat
git add <files>
git diff --cached --stat
git commit -m "map exp <short-id>: <summary>"
git rev-parse --short HEAD
git status --short

map --persona host experiment pre-complete \
  --id <exp-uuid> \
  --metadata .map/generated-plans/experiment-<short-id>-evidence.yaml

map --persona host experiment complete \
  --id <exp-uuid> \
  --summary "结果提交：..." \
  --file .map/generated-plans/experiment-<short-id>-result.md \
  --metadata .map/generated-plans/experiment-<short-id>-evidence.yaml

map --persona host experiment lock release --id <exp-uuid>
map --persona host experiment status --id <exp-uuid>
map --persona host work --notification-category wakeable
```

## 失败时按阶段停下

- `pre-complete` 失败：修 metadata 或验证证据，不要 complete。
- `complete` 失败：实验仍在 `running`，保留锁或释放前写明 blocker。
- `release` 失败：先查 `experiment status` 的 lock 字段，必要时记录 blocker。
- `status` 显示 `result_review`：host 工作结束，等待 reviewer 审批。
- `git status --short` 显示无关改动：不要混合提交；能明确拆分则只提交当前实验文件，不能拆分则写 blocker 并停止。
- `git commit` 失败：不要 complete；先修复验证、lint 或提交边界问题。

## 常见错误处理

| 现象 | 处理 |
|------|------|
| metadata 文件不存在 / YAML 读失败 | 先创建或修正 evidence 文件，再重跑 `pre-complete`；不要把 traceback 当作 MAP 已写入 |
| `complete` 缺 evidence | metadata 至少包含 `pytest_summary`、`alembic_current`、`api_health`、`image_digest` 或 `evidence`；非部署型才显式 `--allow-missing-evidence` |
| lock busy | 用 `experiment status` 看 holder；必要时 `lock skip --next-attempt-at <ISO8601>` 退避 |
| `403` lifecycle | 核对实验是否由当前 host persona 创建；不要换 MCP/admin 代跑 |
| `result_review` 仍出现在清单 | 这是等待 reviewer 的可见性，不是 host 可执行项 |
