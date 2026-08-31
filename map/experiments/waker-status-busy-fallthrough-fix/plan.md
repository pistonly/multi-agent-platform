---
title: "waker status busy 穿透误报 dead 修复（T9-B）：A2 分支短路 + fixture 归真 + 段二 smoke 强制"
acceptance:
  - "A1 穿透修复（cli/waker_status_view.py `compute_waker_state` A2 分支）：pid 校验优先级 → busy_started_at 短路优先级 → gap 判定的三段顺序固化；pid 缺失 / ESRCH / defunct 一律直接 dead（不绕 busy 短路）；pid 存活 + busy_age ≤ busy_stale_w → 短路返回 busy/live（不进入 gap 判定）；pid 存活 + busy_age > busy_stale_w → 升级 stale"
  - "A2 fixture 归真（tests/test_waker_status_view.py 修正 + 新增 4 case 共 5 case）：(a) T9 case (a) 修正：busy_age=2s + last_poll_at=busy_started_at + pid alive → live/busy；(b) busy 穿透回归 1：busy_age=600s + last_poll_at=busy_started_at + pid alive → busy（非 dead）；(c) busy 穿透回归 2：busy_age=1860s + last_poll_at=busy_started_at + pid alive → stale；(d) pid zombie：busy_age=600s + last_poll_at=busy_started_at + pid defunct → dead（zombie 优先级高于 busy 短路）；(e) 旧 state 兼容：state.json 无 busy_started_at → 维持原 gap 判定行为不变"
  - "A3 段二 smoke 强制（log.md 结构化字段）：本实验 log.md 必须包含「段二」一节，记录监督者名字 + 时间戳 + 实测命令（`map waker status` 完整输出）+ `map work` 对照输出 + 同帧一致性结论；缺段二记录的 log.md 在 result_review 阶段被 reviewer reject-result 拦下（host / participant 不豁免）"
  - "A4 pytest 全量绿（基线 1901 passed / 2 skipped / 359 deselected，2026-08-31 监督者实测，只增不减，0 failed）；ruff check 0；git diff 白名单 `^cli/`、`^tests/`；涉及 cli 改动，验收通过后由监督者重启 waker 生效（daemon restart，无 docker build）"
  - "A5 边界：只动 `compute_waker_state` A2 分支短路逻辑 + 对应测试；不改 T9 已修的 fallback chain（lib/waker_state.py schema 注释不动）；不改 server T2 busy 容忍公式；不动 .cursor/skills/ 实验 skill；state.json 字段不变（无 busy_started_at 的旧 state 行为不变）"
evidence_keys:
  - "实测输出：(a) T9 case (a) 修正 fixture busy_age=2s + last_poll_at=busy_started_at + pid alive → map waker status 判 live/busy；(b) busy 穿透回归 fixture busy_age=600s + poll 冻结 + pid alive → busy（非 dead，与修复前对比）；(c) busy 超阈值 fixture busy_age=1860s + pid alive → stale；(d) pid zombie fixture busy_age=600s + pid defunct → dead（zombie 优先级）；(e) 旧 state 兼容 fixture state.json 无 busy_started_at → 维持原 gap 判定"
  - "grep 核证：cli/waker_status_view.py `compute_waker_state` A2 分支含 pid 校验 + busy 短路 + 超阈值升级 + gap 兜底四段顺序；tests/test_waker_status_view.py 含 ≥5 case（修正 case (a) + 4 新增）"
  - "pytest_summary：≥5 case 全过（tests/test_waker_status_view.py），全量 pytest -q 0 failed；ruff check 0"
  - "段二 smoke 记录：本实验 log.md 段二节含监督者实测命令与同帧一致性结论；result_review 阶段 reviewer 据此校验"
  - "Round 1 共识收口：map/topics/waker-status-busy-fallthrough-fix/round1-host.md（host 任务书）+ round1-participant.md（participant §1-§7 支持+强化建议）"
dependencies:
  - "话题 waker-status-busy-fallthrough-fix（48689d56）Round 1 共识收口（已 ready）"
  - "现有 cli/waker_status_view.py `compute_waker_state` A2 分支（L229-232）作为待修复基线"
  - "T9 d12c328c 实验已落地 fallback chain 修正（lib/waker_state.py schema + cli/waker_status_view.py 三段 fallback）；本次不动 fallback chain，只补 A2 分支短路"
  - "T9 §6 is_zombie 双重校验：`os.kill(pid, 0) and not is_zombie(pid)`（读 /proc/<pid>/status 的 State）"
  - "T6 a8b64c20 idle 档已对齐 + T9 d12c328c busy 档阈值已修正；T9-B 补 busy 档穿透盲区"
  - "T9 case (a) fixture `last_poll_gap_s=2.0` 是 bug 漏检根因（绕过穿透路径）"
  - "验收通过后由监督者重启 waker 生效（result_review 阶段 action_items 显式收口，避免验收通过却未生效）"
---

# waker status busy 穿透误报 dead 修复（T9-B）

## 背景

### 现象（实测）
T9（实验 d12c328c / commit 7b66a38）修复 fallback 偷换语义后，真实 3-waker 环境仍误报。2026-08-31T10:15:51Z 实测：host busy 624s（pid 3581308 存活、busy_started_at=10:05:27），`map work`（server T2 口径）正确判 `busy`，`map waker status` 判 **dead**。

最小复现（监督者直调 `compute_waker_state`）：

```text
busy 624s + last_poll 冻结在 busy 起点（真实 waker 忙时不轮询）→ dead  ← 错
busy 624s + last_poll 2 秒前（T9 case (a) fixture 场景）         → live  ← 单测只覆盖了这个
```

### 根因
`cli/waker_status_view.py` `compute_waker_state` A2 分支（L229-232）只在 `busy_age > busy_stale_w` 时返回 stale；**busy 未超阈值时无短路返回**，穿透到 L242-247 的 gap 判定。真实 busy 期间 `last_poll_at` 冻结在 busy 起点，gap=busy_age=624s > dead_w=10×active_interval=300s → dead。

T9 case (a) fixture `last_poll_gap_s=2.0` 未编码话题要求的「poll 暂停」（见 tests/test_waker_status_view.py `_state()` 默认值与 case (a) 注释自述 gap=2 → live），恰好绕过穿透路径：单测全绿、真实场景漏检。

另：T9 自身 commit 给 experiment-host skill 加了二段式验收硬约束（I7），但 T9 自己的验收跳过了段二真实 smoke。**T9-B 必须强制执行段二 smoke**，避免重蹈覆辙。

### Round 1 共识（host + participant）
- ✅ 根因分析 + 任务三件套方向（穿透短路 + fixture 归真 + 段二 smoke 强制）
- ✅ zombie 优先级：`os.kill(pid, 0) and not is_zombie(pid)` 双重校验；defunct pid 直接 dead（不绕 busy 短路）
- ✅ fixture 矩阵升级到 5 case（修正 case (a) + 4 新增回归）覆盖完整边界
- ✅ log.md 段二结构化字段：监督者名字 + 时间戳 + 实测命令输出 + map work 对照 + 同帧一致性结论
- ✅ 范围白名单 `^cli/` `^tests/`；不动 `lib/` `skill/` `server/`；向后兼容（无 busy_started_at 的旧 state 行为不变）

### Round 1 Summary 说明（CLI 事故痕迹，与 T9 同源）
原计划独立 Summary 文件 `round1-summary-host.md` 路径被 CLI 误映射为 `round1-host.md`（immutable convention 解析异常，与 T9 d12c328c 同源）。已删除 orphan 文件。**Summary 内容已融入本 plan.md background 段 + 实验 close_note**，不再单独成文。

## 任务

1. **穿透修复**：`compute_waker_state` A2 分支三段顺序固化：pid 校验（缺失/ESRCH/defunct → dead）→ busy 短路（pid 存活 + busy_age ≤ busy_stale_w → 返回 busy/live）→ 超阈值升级（busy_age > busy_stale_w → stale）→ gap 兜底（其他情况按现有逻辑）。pid 优先级最高，不绕 busy 短路
2. **fixture 归真**：T9 case (a) 改为 `last_poll_at = busy_started_at`（真实编码 poll 冻结）；新增 4 条回归 fixture（穿透回归 600s + 超阈值回归 1860s + zombie + 旧 state 兼容）共 5 case
3. **段二 smoke 强制**：本实验 log.md 必须包含「段二」一节，记录监督者真实 3-waker smoke 结果（host 长会话 >5min 期间 `map waker status` 与 `map work` 同帧一致）；result_review 阶段 reviewer 据此校验，缺段二记录被 reject-result

## 实施步骤

### I1 穿透修复（cli/waker_status_view.py `compute_waker_state` A2 分支）

- 三段顺序固化（在 L229-232 之后插入短路块）：
  1. **pid 校验**：`pid is None or not os.kill(pid, 0) or is_zombie(pid)` → 直接返回 `dead`（与 T9 §6 一致）
  2. **busy 短路**：`busy_age <= busy_stale_w`（含 `busy_started_at` 有效）→ 返回 `busy` / `live`（**不进入** gap 判定）
  3. **超阈值升级**：`busy_age > busy_stale_w` → 返回 `stale`
- 已有 gap 判定逻辑（L242-247）作为兜底（pid 缺失但 busy_age 短等异常组合）
- 注释清楚三段顺序 + 为什么 busy 短路优先级高于 gap 判定（避免后续维护再次踩坑）

### I2 fixture 归真（tests/test_waker_status_view.py 修正 + 新增 4 case）

- 修正 T9 case (a)：`_state()` 默认 `last_poll_gap_s=2.0` 改为 `last_poll_at=busy_started_at`；注释更新为「真实 waker busy 期间 poll 冻结语义」
- 新增 case (b) busy 穿透回归 1：busy_age=600s + last_poll_at=busy_started_at + pid alive → 期望 busy（非 dead）
- 新增 case (c) busy 穿透回归 2：busy_age=1860s + last_poll_at=busy_started_at + pid alive → 期望 stale
- 新增 case (d) pid zombie：busy_age=600s + last_poll_at=busy_started_at + pid defunct → 期望 dead（zombie 优先级）
- 新增 case (e) 旧 state 兼容：state.json 完全删掉 `busy_started_at` 字段 → 维持原 gap 判定（无 `busy_started_at` → 不短路）
- mock datetime / freezegun 注入时间戳，避免真 sleep

### I3 段二 smoke 强制（log.md 结构化字段）

- 本实验 log.md 必须包含「段二」一节，结构：
  - 监督者名字（supervisor 标识）
  - 时间戳（实测发生时刻 ISO 8601）
  - 实测命令：`map waker status` 完整输出
  - 对照命令：`map work` 完整输出
  - 同帧一致性结论：busy 档两边一致 / 不一致
- 测试场景：真实 3-waker 环境 host 长会话（含 todo 巡检或多话题批处理）持续 >5min
- result_review 阶段 reviewer 校验：log.md 缺段二节 → reject-result

## 验收对照（任务书 §机器可判验收要求）

- ✅ 单测：修正后 case (a) + 4 新增 ≥5 case，全部 green
- ✅ 全量：.venv/bin/python3 -m pytest tests/ -q；基线 1901 passed / 2 skipped / 359 deselected，只增不减，0 failed
- ✅ 段二 smoke（必须，监督者执行）：真实 3-waker 环境 host busy >5min 时，`map waker status` 不显示 stale/dead，与 `map work` 同帧口径一致
- ✅ git diff --name-only 白名单：`^cli/`、`^tests/`

## 边界

- 只动 `compute_waker_state` A2 分支短路逻辑 + 对应测试；不改 T9 已修的 fallback chain（lib/waker_state.py schema 注释不动）
- 不改 server 侧 T2 busy 容忍公式
- 不动 .cursor/skills/ 实验 skill（I7 二段式条款本次仅执行，不镜像到 topic-host）
- state.json 字段不变（向后兼容：无 busy_started_at 的旧 state 行为不变）
- 涉及 cli 改动，验收通过后由监督者重启 waker 生效（result_review 阶段 action_items 显式收口）

## 不在范围内

- I7 二段式条款镜像到 topic-host SKILL.md（participant §6 提议；下次话题决策）
- T9 §6 is_zombie 与 T9-B 短路顺序统一 zombie 走 dead 的判定文档（同上）
- busy 期间 last_poll_at 冻结现象在 waker status 视图显式可见（同上）
