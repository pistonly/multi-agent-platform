---
experiment_id: d12c328c-46ad-4ce7-8440-27fc52d7c8a8
title: "waker status busy 档口径修正：expected_remind_runtime 真实同源 + 验收模板二段式"
status: running
slug: waker-status-busy-threshold-fix
---

# 实验 d12c328c 执行日志

## 实施步骤

### I1 state.json 序列化 expected_remind_runtime_seconds ✅

**修改**：
- `cli/simple_waker.py:198-235` `SimpleWakerConfig` 新增 `expected_remind_runtime_minutes: int | None = None`
- `cli/simple_waker.py:721-737` `__init__` 从 env `MAP_EXPECTED_REMIND_RUNTIME_MINUTES` 解析；非法值 RuntimeWarning + fallback 30min
- `cli/simple_waker.py:746-754` 加载 state 后 `persona_state.setdefault("expected_remind_runtime_seconds", minutes * 60)` + 立即 atomic save
- `lib/waker_state.py:60-98` 新增 `WAKER_STATE_SCHEMA` dataclass + `EXPECTED_REMIND_RUNTIME_FALLBACK_SECONDS=1800` + `MAP_EXPECTED_REMIND_RUNTIME_ENV` 常量（schema 锚点 + 优先级链文档化）

### I2 CLI fallback chain 重构 ✅

**修改**：`cli/waker_status_view.py:146-184` 新增 `_resolve_expected_remind_runtime` 三层优先级链；`cli/waker_status_view.py:222-226` `compute_waker_state` 用真实同源 1800s 替换旧 `expected_remind_runtime = idle_stale_w ≈ 180s` 偷换语义。

**优先级**：
1. `persona_state["expected_remind_runtime_seconds"]`（waker 启动时写入）
2. env `MAP_EXPECTED_REMIND_RUNTIME_MINUTES`
3. `EXPECTED_REMIND_RUNTIME_FALLBACK_SECONDS` = 1800s（30min default，与 server `Settings.expected_remind_runtime_minutes=30` 同源）

**绝不**回退到 `idle_stale_w`（那是 fallback 偷换语义，正是修复的根因）。

### I3 atomic write 重试（reader 容错）✅

**修改**：`cli/waker_status_view.py:301-337` `_load_state_file` 遇 `JSONDecodeError` 重试一次（writer 已 atomic，reader 容错一次够用）。writer atomic write 已在 `cli/bridge_state.py:33-39` 既有（tmp + os.replace），新增 `tests/test_waker_state_atomic.py` 4 case 覆盖 writer 侧契约。

### I4 pid zombie 排除 ✅

**修改**：`cli/waker_status_view.py:113-143` 新增 `_is_zombie(pid)` 读 `/proc/<pid>/status` State 字段（Z = defunct）；`_pid_alive` 增加 zombie 排除 → defunct pid 升级 stale/dead。

### I5 schema 注释 ✅

**修改**：`lib/waker_state.py` 完整 dataclass schema（`WAKERStateField` name/type/fallback/introduced_version）；5 字段全文档化（含 `expected_remind_runtime_seconds` v0.15 引入 + 三层 fallback chain 注释）。

### I6 fixture-based 同帧测试 ✅

**新增**：`tests/test_waker_status_view.py` 12 case 覆盖 6 I6 路径 + 验收 fixture：
- (a) busy 5min + pid 存活 → live（修复前 busy 300s 误报 stale）
- (b) busy 1900s → stale（> 30min server 容忍）
- (c) state.json 缺 `expected_remind_runtime_seconds` + env 缺 → 30min default
- (d) pid defunct → dead（I4 排除 zombie）
- (d2) `_is_zombie` 真实路径（fake /proc/<pid>/status Z state）
- (e1-e4) fallback chain 三层：state.json > env > 30min default + env 非法 fallback
- (f) atomic write race reader 重试成功
- (f2) 持久损坏 → 空 dict + RuntimeWarning
- acceptance 3-waker busy 5min 全 live（host/participant/reviewer 一致）

**新增**：`tests/test_waker_state_atomic.py` 4 case 覆盖 writer atomic write（tmp + os.replace 契约 + 不留 .tmp + 连续写串行安全 + mkdir parents）。

**更新**：`tests/test_waker_status.py::test_busy_stuck_tier` + `test_b_busy_stuck_tier_table` fixture 升级到新阈值 1800s（旧 600s 编码了 fallback 偷换语义 bug，已废弃）。

### I7 验收模板硬约束 ✅

**修改**：`.cursor/skills/experiment-host/SKILL.md` 新增「视图类实验验收 checklist 二段式」段：

- **段一**：synthetic fixture（pytest 跑，CI 自动化）— 覆盖代码层所有分支
- **段二**：真实环境 smoke（监督者手动确认，CLI 包入口 ≠ pytest 直接 import 模块）— 真实 3-waker 长会话 busy 同帧对齐 + 监督者重启 server + waker 收口

固化 T6 a8b64c20 教训：idle 档同帧一致 / busy 档漏核导致 d12c328c 修复任务。

### I8 commit + log + release

- ✅ pytest 全量：1892 passed / 2 skipped / 359 deselected（基线 1875 + 我新加 17 case，0 failed）
- ✅ ruff check 0（cli/waker_status_view.py + cli/simple_waker.py + lib/waker_state.py + 3 个 test 文件）
- ✅ git diff 白名单：^cli/、^lib/、^tests/、^.cursor/skills/（无关 pyproject.toml 改动已 revert）
- 窄 commit：见 git log（commit message 含 I1-I7 子项）
- 实验日志：本文件

## 验收对照（plan acceptance 10 条）

| 编号 | 要求 | 实测 |
|------|------|------|
| A1 | state.json 序列化 expected_remind_runtime_seconds + 向后兼容 | I1 ✅ |
| A2 | CLI fallback chain 三层 + 禁止回退 idle_stale_w | I2 ✅ |
| A3 | 优先级链固化（dataclass 字段标注） | I5 ✅ |
| A4 | atomic write + reader JSONDecodeError 重试 + tests/test_waker_state_atomic.py | I3 ✅ |
| A5 | pid 存活判定 + zombie 排除 | I4 ✅ |
| A6 | fixture-based ≥6 case | I6（12 case）✅ |
| A7 | experiment-host skill 视图类实验 checklist 二段式 | I7 ✅ |
| A8 | 3-waker smoke：host smoke 真实环境 + participant/reviewer pytest | I6 acceptance + 真实 smoke 待监督者 |
| A9 | pytest 全量绿 + ruff check 0 + git diff 白名单 | I8 ✅ |
| A10 | 不改 server T2 busy 容忍公式 + 不改 live/idle_stale/dead + 监督者重启 | server 0 改动；cli 改动验收后由监督者重启 server + waker（**action_item 显式收口**） |

## 机器可判验收（实测复跑）

```bash
# 全量 pytest（实际跑通，命令 + 输出）
.venv/bin/python3 -m pytest tests/ -q --tb=no
# → 1892 passed, 2 skipped, 359 deselected, 22 warnings in 445.70s (0:07:25)
# → 0 failed（基线 1875 + I6 新加 17 case，0 regression）

# ruff check
.venv/bin/python3 -m ruff check \
    cli/waker_status_view.py cli/simple_waker.py lib/waker_state.py \
    tests/test_waker_status_view.py tests/test_waker_state_atomic.py tests/test_waker_status.py
# → All checks passed!

# 6 路径实测
.venv/bin/python3 -m pytest tests/test_waker_status_view.py -v
# → 12 passed in 0.17s（6 case (a)-(f) + 6 边界/acceptance）

# writer atomic write
.venv/bin/python3 -m pytest tests/test_waker_state_atomic.py -v
# → 4 passed in 0.17s
```

## Round 1 Summary 说明（CLI 事故痕迹）

原计划独立 Summary 文件 `round1-summary-host.md` 路径被 CLI `topic comment --force --round-summary --file round1-summary-host.md` 误覆盖为 `round1-host.md`（immutable convention 解析异常）。已用 Write 恢复 `round1-host.md` 原文（T9 任务书），orphan `round1-summary-host.md` 作为事故痕迹保留。Summary 内容已融入本 log.md 实施步骤 + plan.md background 段。

## Action Items（result_review 阶段监督者收口）

1. **监督者重启 server + waker 生效**（验收通过后必须）—— cli 改动 daemon restart 即可，无需 docker build（`Dockerfile.api` 不含 cli/ 代码；重启 .venv 上的 waker 进程生效）
2. 真实环境 smoke：监督者手动跑 `map --persona host waker status` + 进长会话验证 busy 同帧对齐
3. 若监督者发现 busy 5min 仍误报 stale，停止部署并联系实验负责人

## Co-author

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
