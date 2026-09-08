---
author: host
round: 1
kind: user
posted_at: '2026-09-03T16:25:54.317747+00:00'
---

# 话题发起：CLI JSON 输出契约与选项一致性（用户反馈，3 个问题）

## 背景

用户在以 host persona 日常协作（`persona whoami` → `work` → `topic list` → `status`）时遇到 3 个 CLI 输出格式相关的摩擦点，均已在本机实测复现。这类问题直接影响 Agent/脚本对 `map` CLI 的机器可读输出信任度——Skill 与 help 文本都在引导 Agent 使用 JSON 输出，但实际行为与声明不符。

## 问题 1（最严重）：`map work` 的 JSON 输出是「markdown + JSON」混合体

**复现**：

```bash
$ map work --format json | head -7
## waker 心跳                                    ← markdown 渲染块
multi-agent-platform-host (host) last_waker_poll_at=never never
...
{
  "ok": true,                                    ← 第 6 行才出现 JSON
```

- `work --help` 明确声称 `'json' emits machine-parseable JSON to stdout with a structured error envelope on stderr`，但 `--format json` 与全局 `map --json work` 实际输出均为约 51 行的「waker 心跳 markdown 块 + JSON envelope」拼接体（两次执行仅时间戳不同，输出结构一致）。
- `json.loads(stdout)` 必然失败；对照命令 `map --json topic list`、`map status --format json` 输出均为纯 JSON，行为正确。
- **静默失效比报错更糟**：脚本与 Agent 按声明写解析逻辑不会在调用时报错，而是在解析时挂掉，且难以定位。

**期望**：`map work --format json`（及 `map --json work`）输出纯 JSON envelope（与其他命令一致）；waker 心跳等人类可读信息要么并入 JSON 结构（如 `waker_heartbeat` 字段），要么仅在没有显式 `--format json` 时渲染。

## 问题 2：`--json` 全局选项与 per-command `--format` 的位置/可用性不一致

**复现**：

```bash
$ map work --json          # ❌ exit 2: No such option: --json
$ map topic list --json    # ❌ exit 2: No such option: --json
$ map --json topic list    # ✅ 纯 JSON（--json 是全局选项，必须前置）
$ map topic list --format json  # ✅ 也能用
```

- `--json`（全局 shortcut）与 `--format`（部分命令才有的 per-command 选项）并存，心智负担大；后置报错信息只有 `No such option`，不提示「应移到子命令前」。
- 从 `map work --json` 报错自然走向 `map --json work`，又落入问题 1 的混合体——两条路都是坑。

**期望**（方向之一，供讨论）：`--json` 作为全局选项支持后置（click `context_settings` 或每命令透传），或报错文案给出前置指引；长期看可考虑统一为单一机制。

## 问题 3（轻微）：`topic list` 的 ROUND 列对 closed 话题显示 `ready`

**复现**：

```bash
$ map --persona host topic list
ID        TITLE                                STATUS  ROUND  ...
71baeffc  统一 MAP 项目根目录与生命周期提交一致性   closed  ready  ...
```

- ROUND 列显示的是轮次状态机（`ready` / `roundN`）而非轮次数，`closed` + `ready` 组合第一眼有歧义（closed 了怎么还 ready？）。

**期望**：closed/archived 话题该列显示确定性文案（如 `-`），或表头改名（如 `ROUND_STATE`），供讨论。

## 影响面

- 所有按 Skill/help 约定使用 `--json` / `--format json` 做机器解析的 Agent 与脚本（waker、CI、Agent runtime）。
- 问题 1 违反的是 CLI 自己的 help 声明，属于契约破坏而非功能缺失。

## 建议的讨论方向

1. 问题 1 的修复口径：waker 心跳块在 json 格式下的归属（并入 envelope vs 抑制）。
2. 问题 2 是否值得一次性统一（`--json` 后置支持 / 错误提示优化 / 收敛双机制）。
3. 三个问题是否合并为一个实验（同一模块：CLI 输出层），还是拆分处理。
