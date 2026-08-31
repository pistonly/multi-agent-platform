---
title: "waker runtime skill 热自检：运行中漂移检测 + 重同步 + 审计留痕"
acceptance:
  - "A1 周期自检触发：`cli/simple_waker.py` 主循环每 N cycle（env `WAKER_DRIFT_CHECK_INTERVAL_CYCLES`，默认 30）调用一次 drift 检测；与现有 waker 拉 work + 渲染心跳解耦，不阻塞轮询"
  - "A2 漂移检测粒度：维护 `last_seen_mtime: dict[skill_relpath, (mtime_ns, size)]` 内存缓存；per-skill 单文件粒度比较（`<skill>/SKILL.md` 与 `<skill>/references/*.md` 单独比较，不取目录 mtime）；比较源 `.cursor/skills/<rel>` 与副本 `.map/claude-runtime-home-<persona>/.claude/skills/<rel>`"
  - "A3 双维度快检：mtime（st_mtime_ns）+ size，任一不一致再 hash（sha256）二次确认；容差 `mtime_delta_ns <= 1_000_000`（1ms）+ size 严格相等才算一致"
  - "A4 立即重同步：发现 drift 立即调用 `sync_runtime_skills`；同周期同 skill 仅触发一次（`last_resync_at[skill]` 抑制抖动）；sync 期间不进 busy 状态（与 b3ec2e4d busy 拆分协调，sync < 1s 完成）"
  - "A5 启动留痕：waker 启动时 `sync_runtime_skills` 调用结果写入日志，固定 JSON schema：`{\"event\": \"startup_sync\", \"ts\": \"...\", \"skills_count\": N, \"synced_skills\": [\"a\",\"b\"], \"skipped_reason\": null|enum}`，skipped_reason 枚举 `source_missing` / `permission_denied` / `disabled`"
  - "A6 运行中重同步留痕：drift 检测到 + 立即重同步 → 写日志：`{\"event\": \"drift_resync\", \"ts\": \"...\", \"drift_skills\": [\"c\"], \"resync_result\": \"ok\"|\"failed\", \"duration_ms\": N}`；失败事件额外 `{\"event\": \"drift_resync_failed\", ..., \"error\": \"PermissionError\", \"alert\": true}`，alert 通道独立告警不阻断轮询"
  - "A7 验收 case 6 条全过：(a) 漂移检测重同步 / (b) 一致零开销（mock 验证连续 N 周期 copytree 调用次数 = 0）/ (c) 重同步失败（chmod 0444 → alert + 轮询继续）/ (d) 周期可配置（env override）/ (e) 启动留痕（含 skipped_reason）/ (f) 跨平台 mtime 误判抑制（mtime 差 1ns + size 一致 → 不触发 hash）"
  - "A8 ruff check 0；pytest 全量绿（基线 1782 passed + 新增，只增不减，0 failed）；git diff 白名单 `^cli/`、`^tests/`；既有 sync_runtime_skills 全量镜像语义与启动同步行为不动；不影响 83bf610 + 8b1d20a1 + b3ec2e4d 已落地链路"
  - "A9 边界：不改 sync_runtime_skills 的全量镜像语义（rmtree+copytree+孤儿清理）只加触发时机；不引入新 server 端点 / DB 字段；纯 waker 本地行为；不引入新的 wake signature / work_items kind"
evidence_keys:
  - "pytest_summary:新增 6 case 全过（test_simple_waker_drift_detection.py 或追加到 test_simple_waker.py），全量 pytest -q 0 failed"
  - "实测输出:(a) mock 修改 .cursor/skills/<skill>/SKILL.md → waker 下一周期触发 sync_runtime_skills + 日志含 drift_skills + 副本 mtime 同步；(b) mock 无修改 → 连续 N 周期 copytree 调用次数 = 0；(c) mock chmod 0444 → drift_resync_failed event + alert=true + 轮询继续；(d) env WAKER_DRIFT_CHECK_INTERVAL_CYCLES=5 override → 每 5 cycle 触发；(e) waker 启动 → 日志含 startup_sync + skills_count + synced_skills[] + skipped_reason 字段；(f) mock mtime 差 1ns + size 一致 → 不触发 hash 二次确认"
  - "grep 核证:cli/simple_waker.py 主循环含 drift 检测调用；cli/wake_backend.py sync_runtime_skills 返回值或包装层加入 startup_sync 日志字段；新增 drift_cache (last_seen_mtime + last_resync_at) 内存 dict"
dependencies:
  - "话题 waker-skill-drift-hotcheck（0251c6e2-2b0c-5e0d-9367-286f8dd8e6c9）Round 1+2 共识收口"
  - "现有 cli/wake_backend.py:sync_runtime_skills（line 155）全量镜像同步机制"
  - "现有 cli/simple_waker.py:1276 启动时同步调用点"
  - "现有 cli/simple_waker.py 主循环 cycle 调度结构"
  - "现有 waker 渲染层 cli/waker_heartbeat_render.py（实验 b3ec2e4d 已落地）— sync 期间不进 busy 状态协调"
  - "现有 logger 审计接口（与 8b1d20a1 通知 fan-out 链路解耦）"
  - "实验 b3ec2e4d 已落地的 busy 状态机 + 容忍阈值 + 渲染（本次 sync_resync 不进 busy 需与该实验协调）"
  - "验收通过后由监督者重启 server 与 waker 生效（无 docker 镜像 build，仅 daemon restart）"
---

# waker runtime skill 热自检：运行中漂移检测 + 重同步 + 审计留痕

## 背景

本战役 T1 实验改了 `.cursor/skills/`，运行中的 waker 全程持有旧副本，靠监督者验收后人工重启才生效（cli/simple_waker.py:1276 启动时一次性 sync_runtime_skills，无运行中重同步）。同步动作无任何日志/审计留痕，漂移发生过也无法事后察觉。

本实验在 waker 主循环加周期 drift 自检，发现漂移立即重同步（不等下一 cycle 头）+ 启动 + 运行中两类同步均留痕。

## 任务

1. **运行中漂移自检**：每 N cycle（默认 30，env 可配）以双维度快检（mtime_ns + size）比较 `.cursor/skills/` 与 runtime home 副本；不一致再 hash 二次确认；发现 drift 立即 `sync_runtime_skills`
2. **启动 + 运行中留痕**：固定 JSON schema，监督者一眼可见同步状态（skills_count / synced_skills / skipped_reason / drift_skills / resync_result / alert）
3. **窄提交白名单**：`^cli/`、`^tests/`

## 实施步骤

### I1 drift 检测模块（cli/drift_detector.py 新建）

- `DriftDetector` dataclass：持有 `last_seen_mtime: dict[str, tuple[int, int]]`（skill_relpath → (mtime_ns, size)）+ `last_resync_at: dict[str, float]`（抑制抖动）
- `check_drift(source_root, dest_root) -> list[DriftEntry]`：per-skill 单文件粒度遍历（`SKILL.md` + `references/*.md`），比较 mtime_ns + size，不一致返回 drift 列表
- 首次调用全 scan 填充 `last_seen_mtime`；后续周期增量判定

### I2 立即重同步 + 抖动抑制（cli/drift_detector.py）

- `resync(drift_entries) -> ResyncResult`：调 `sync_runtime_skills(source_root, dest_root)`，记录 `last_resync_at[skill]` 抑制同周期重复
- sync 期间**不进 busy 状态**（与 b3ec2e4d 协调；sync < 1s 完成）

### I3 启动留痕（cli/simple_waker.py 启动循环）

- 现有 `sync_runtime_skills` 调用点（cli/simple_waker.py:1276）后追加：
  ```python
  logger.info(json.dumps({
      "event": "startup_sync",
      "ts": datetime.now(timezone.utc).isoformat(),
      "skills_count": len(synced_skills),
      "synced_skills": synced_skills,
      "skipped_reason": None,  # 或 source_missing / permission_denied / disabled
  }))
  ```
- skipped_reason 从 `sync_runtime_skills` 返回值或异常捕获派生

### I4 周期自检触发（cli/simple_waker.py 主循环）

- 在主循环 cycle 计数后增加：
  ```python
  if cycle_count % drift_interval == 0:
      drift_entries = drift_detector.check_drift(SOURCE_SKILLS, RUNTIME_SKILLS)
      if drift_entries:
          result = drift_detector.resync(drift_entries)
          logger.info(json.dumps({
              "event": "drift_resync" if result.ok else "drift_resync_failed",
              ...
              "alert": not result.ok,
          }))
  ```
- `drift_interval` 读 env `WAKER_DRIFT_CHECK_INTERVAL_CYCLES`，默认 30

### I5 跨平台 mtime 容差（cli/drift_detector.py）

- 比较时容差 `mtime_delta_ns <= 1_000_000`（1ms）+ size 严格相等
- 不一致才触发 hash（sha256）二次确认
- 抑制"mtime 差 1ns 但 size 一致"的跨平台误判（验收 case f）

### I6 回归测试（tests/test_simple_waker_drift_detection.py 新建，6 case）

- (a) 漂移检测重同步：mock 改源 skill → 下周期 drift_detector 检测到 + sync_runtime_skills 被调 + 日志含 drift_skills
- (b) 一致零开销：mock 无修改 → 连续 N 周期 copytree/mock 调用次数 = 0
- (c) 重同步失败：mock chmod 0444 副本目录 → drift_resync_failed event + alert=true + 轮询继续
- (d) 周期可配置：env `WAKER_DRIFT_CHECK_INTERVAL_CYCLES=5` → 每 5 cycle 触发
- (e) 启动留痕：waker 启动 → 日志含 startup_sync + skills_count + synced_skills + skipped_reason 字段
- (f) 跨平台 mtime 抑制：mock mtime 差 1ns + size 一致 → 不触发 hash 二次确认

### I7 commit + log + release

- 窄 commit 白名单 `^cli/`、`^tests/`
- ruff check 0 + pytest 全量绿
- complete log → reviewer → done
- cli 改动：监督者重启 waker 生效（无 docker 镜像 build，仅 daemon restart）

## 风险与边界

- 不改 sync_runtime_skills 全量镜像语义（rmtree + copytree + 孤儿清理），只加触发时机
- 不引入新 server 端点 / DB 字段
- 不影响 83bf610 + 8b1d20a1 + b3ec2e4d 已落地链路
- drift_resync 期间不进 busy 状态（与 b3ec2e4d busy 拆分协调）
- 默认 30 cycles（≈ 15 分钟 @ 30s/cycle），< 5 分钟不建议
- 与 8b1d20a1 通知 fan-out 链路解耦：审计日志走独立 logger，不进 notification 通道（避免污染 wakeable 列表）
- 与 waker 渲染层 cli/waker_heartbeat_render.py 协调：drift_resync 期间不刷新 busy=true

## Co-author

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
