---
title: "waker status busy 档口径修正：expected_remind_runtime 真实同源 + 验收模板二段式"
acceptance:
  - "A1 state.json 序列化 expected_remind_runtime_seconds：waker 启动时从 config 项 expected_remind_runtime_minutes + env MAP_EXPECTED_REMIND_RUNTIME_MINUTES 计算并写入 state.json；缺失字段向后兼容（旧 state 不破坏）"
  - "A2 CLI fallback chain（cli/waker_status_view.py:154）：state.json.expected_remind_runtime_seconds > env MAP_EXPECTED_REMIND_RUNTIME_MINUTES > 30min default；**禁止**回退到 idle_stale_w（修复 fallback 偷换语义）"
  - "A3 优先级链固化（lib/waker_state.py schema 注释）：dataclass 字段标注 name / type / fallback / 引入版本；防止后续 T 任务加字段时再偷换"
  - "A4 atomic write（cli/simple_waker.py writer）：写 state.json 走 tmp 文件 + os.replace（POSIX 原子 rename）；CLI 读时 except JSONDecodeError + 重试一次；新增 tests/test_waker_state_atomic.py"
  - "A5 pid 存活判定：os.kill(pid, 0) and not is_zombie(pid)；is_zombie 读 /proc/<pid>/status 的 State（Z=defunct）；defunct pid 升级 stale/dead"
  - "A6 fixture-based 同帧一致性测试（tests/test_waker_status_view.py 新增 ≥6 case）：(a) busy 5min fixture busy_started_at = now()-300s + pid 存活 + poll 暂停 → 判 busy（mock datetime / freezegun，避免真 sleep）；(b) 超阈值 fixture busy_started_at = now()-1900s → 升级 stale；(c) 缺字段 fixture state.json 完全删掉 expected_remind_runtime_seconds → fallback 30min；(d) pid zombie fixture mock defunct → stale；(e) fallback chain 三层 state.json / env / 都没有 → 都返回 30min；(f) atomic write race mock 半截 JSON → CLI 重试成功"
  - "A7 验收模板硬约束（.cursor/skills/experiment-host/SKILL.md 视图类实验 checklist 二段式）：synthetic fixture 覆盖代码层所有分支（pytest 跑）+ 真实环境 smoke 由监督者手动确认（CLI 包入口 ≠ pytest 直接 import 模块）；避免 T6 idle 档同帧一致 / busy 档漏核的教训重演"
  - "A8 3-waker smoke 覆盖范围：host smoke 真实环境手动确认（监督者跑）+ participant / reviewer 两条独立路径由 pytest fixture 覆盖（mock state.json + monkeypatch expected_remind_runtime_seconds 模拟另两个 persona）"
  - "A9 pytest 全量绿（基线 1875 passed / 2 skipped / 359 deselected，只增不减，0 failed）；ruff check 0；git diff 白名单 ^cli/、^lib/、^tests/、^.cursor/skills/"
  - "A10 边界：不改 server T2 busy 容忍公式本身（server 是同源基准）；不改 live / idle_stale / dead 档（T6 已对齐）；cli 改动验收通过后由监督者重启 server 与 waker 生效（daemon restart，无 docker build），result_review 阶段 action_items 显式收口"
evidence_keys:
  - "实测输出：(a) mock busy_started_at = now()-300s → map waker status 判 busy；(b) mock busy_started_at = now()-1900s → 升级 stale；(c) state.json 缺字段 → fallback 30min（不偷换 idle_stale）；(d) mock pid defunct → 升级 stale；(e) pytest fixture 三层 fallback 都返回 30min；(f) mock 半截 JSON → CLI 重试一次成功"
  - "grep 核证：cli/waker_status_view.py 含 fallback chain 三段；lib/waker_state.py 含 dataclass schema 注释；cli/simple_waker.py 写 state.json 走 tmp + os.replace；tests/test_waker_state_atomic.py 含 atomic write case；tests/test_waker_status_view.py 含新 ≥6 case；.cursor/skills/experiment-host/SKILL.md 含视图类实验二段式 checklist"
  - "pytest_summary：≥6 case 全过（tests/test_waker_status_view.py + test_waker_state_atomic.py），全量 pytest -q 0 failed"
  - "experiment-host skill 视图类实验 checklist 二段式模板已写入 .cursor/skills/experiment-host/SKILL.md"
  - "round1-summary-host.md 事故痕迹：原计划独立 Summary 文件路径被 CLI --force 误覆盖 round1-host.md（已恢复），本实验 Summary 融入 plan.md background 段"
dependencies:
  - "话题 waker-status-busy-threshold-fix（b446d8ed）Round 1 共识收口（已 ready）"
  - "现有 cli/waker_status_view.py:154 busy_stale fallback（待修复）"
  - "现有 state.json waker 进程状态（cli/simple_waker.py 现有 writer，待改 atomic）"
  - "server 侧 T2 busy 容忍公式（server/services/status_service.py）作为同源基准"
  - "实验 T6（a8b64c20）idle 档已对齐；本次 busy 档对齐后 waker status 视图完整闭环"
  - "现有 lib/waker_state.py（若存在）扩展为 dataclass schema 注释承载"
  - "验收通过后由监督者重启 server + waker 生效（result_review 阶段 action_items 显式收口，避免验收通过却未生效）"
---

# waker status busy 档口径修正：expected_remind_runtime 真实同源 + 验收模板二段式

## 背景

### 现象（实测）
T6（a8b64c20）修复 idle 档误报后，busy 档仍误报：waker 进长会话（busy 超 3 分钟）时 `map waker status` 升级 stale、超 5 分钟标 dead；同时刻 `map work`（server T2 口径）正确显示 busy。监督者巡检期间 host 长会话常态触发，CLI 视图在最关键的「waker 正忙」场景不可用。

### 根因
`cli/waker_status_view.py:154` busy 升级阈值 `busy_stale(expected_remind_runtime=idle_stale_w, idle_threshold=idle_stale_w)` = 2×idle_stale = 180s——expected_remind_runtime 缺省 fallback 偷换语义。server 侧 T2 口径（server/services/status_service.py）是 `max(expected_remind_runtime_minutes=30min, 2×idle_threshold)`。T6 任务书「busy 升级阈值与 server 侧 busy 容忍同源」未落实（T6 验收只测了 idle 档同帧一致性，busy 档漏核——监督者验收盲区）。

### Round 1 共识（host + participant）
- ✅ 根因分析 + 任务三件套方向（state.json 序列化 + fixture-based 同帧测试 + 验收模板硬约束）
- ✅ fixture 设计：mock datetime / freezegun 注入 busy_started_at，避免真 sleep 拖慢 pytest
- ✅ 优先级链固化：state.json > env > 30min default（落 `lib/waker_state.py` schema 注释）
- ✅ 验收模板二段式：synthetic fixture + 真实环境 smoke（落 `.cursor/skills/experiment-host/SKILL.md`）
- ✅ 3-waker smoke 覆盖：host smoke 真实环境 + participant/reviewer pytest fixture
- ✅ pid 存活判定：`os.kill(pid, 0) and not is_zombie(pid)`（双重校验）
- ✅ atomic write 采纳为主线 I3（修好 busy 档后 race 暴露概率上升，race 显式化收益高）
- ✅ action_items 收口：监督者重启 server + waker 显式列入 result_review 阶段

### Round 1 Summary 说明（CLI 事故痕迹）
原计划独立 Summary 文件 `round1-summary-host.md` 路径被 CLI `topic comment --force --round-summary --file round1-summary-host.md` 误覆盖为 `round1-host.md`（immutable convention 解析异常）。已用 Write 恢复 `round1-host.md` 原文（T9 任务书），orphan `round1-summary-host.md` 作为事故痕迹保留。**Summary 内容已融入本 plan.md background 段 + 实验 close_note**，不再单独成文。

## 任务

1. **真实同源**：CLI busy 容忍 = state.json.expected_remind_runtime_seconds > env > 30min default（与 server T2 同源）
2. **fixture-based 同帧测试**：合成 state.json（避免真 sleep）覆盖 < 阈值 / > 阈值 / 缺字段 / pid zombie / fallback chain 三层 / atomic write race 六条路径
3. **验收模板硬约束**：experiment-host skill 视图类实验 checklist 二段式（synthetic + 真实 smoke）
4. **state 原子写 + pid zombie 排除**：tmp → os.replace 写；JSONDecodeError 重试一次；is_zombie(pid) 升级 stale
5. **优先级链文档化**：lib/waker_state.py schema 注释固化字段 + fallback + 版本

## 实施步骤

### I1 state.json 序列化 expected_remind_runtime_seconds（cli/simple_waker.py writer）

- 启动时 waker 计算 seconds = config.expected_remind_runtime_minutes × 60（或 env 覆盖）
- 写入 state.json（含 last_seen_mtime 兼容旧 state）
- 缺字段时：CLI 读取走 env fallback（不回退到 idle_stale）
- CLI 读取逻辑：state.json > env MAP_EXPECTED_REMIND_RUNTIME_MINUTES > 30min default

### I2 CLI fallback chain 重构（cli/waker_status_view.py:154）

- 删除 idle_stale_w fallback 偷换语义
- 实现三段优先级链
- pytest fixture 三层各跑一次（state.json / env / 都没有 → 都得返回 30min）

### I3 atomic write（cli/simple_waker.py writer）

- 写 state.json：tmp 文件 + os.replace（POSIX 原子 rename）
- CLI 读：except JSONDecodeError + 重试一次
- 新增 tests/test_waker_state_atomic.py

### I4 pid 存活判定（cli/waker_status_view.py）

- os.kill(pid, 0) and not is_zombie(pid)
- is_zombie 读 /proc/<pid>/status 的 State
- defunct pid 升级 stale/dead

### I5 schema 注释（lib/waker_state.py）

- dataclass 字段标注：name / type / fallback / 引入版本
- 后续 T 任务加字段时复用此注释模板

### I6 fixture-based 同帧测试（tests/test_waker_status_view.py 新增 ≥6 case）

- (a) busy 5min fixture：合成 state.json busy_started_at = now() - 300s + pid 存活 + poll 暂停 → busy
- (b) 超阈值 fixture：busy_started_at = now() - 1900s → stale
- (c) 缺字段 fixture：state.json 完全删掉 expected_remind_runtime_seconds → fallback 30min
- (d) pid zombie fixture：mock pid defunct → stale
- (e) fallback chain 三层：state.json / env / 都没有 → 都返回 30min
- (f) atomic write race：mock 半截 JSON → CLI 重试成功

### I7 验收模板硬约束（.cursor/skills/experiment-host/SKILL.md）

- 视图类实验 checklist 段：
  - synthetic fixture 覆盖代码层所有分支（pytest 跑）
  - 真实环境 smoke 由监督者手动确认（CLI 包入口 ≠ pytest import 模块）
- 二段式模板固化（避免 T6 idle 档同帧一致 / busy 档漏核的教训重演）

### I8 commit + log + release

- 窄提交白名单 ^cli/、^lib/、^tests/、^.cursor/skills/
- ruff check 0 + pytest 全量绿
- result_review 阶段显式列入 action_items：监督者重启 server + waker

## 风险与边界

- 不改 server T2 busy 容忍公式本身（server 是同源基准）
- 不改 live / idle_stale / dead 档（T6 已对齐）
- state.json 新增字段向后兼容（缺字段 fallback，不破坏旧 state）
- cli 改动需监督者重启 server 与 waker 生效（result_review action_items 显式收口）

## Co-author

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
