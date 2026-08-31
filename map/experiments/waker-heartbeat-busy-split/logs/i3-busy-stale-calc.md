## I3 — busy/stale 计算（status_service.py + schema）

### 改动文件

- `sdk/python/map_types/schemas/project.py` `WakerHeartbeatRead` 新增 `last_busy_since: datetime | None = None`（默认 None 保持后向兼容）
- `server/services/status_service.py` `build_waker_heartbeats` 重写 stale 计算：
  - `last_busy_since` 非空 → busy，用 busy_tolerance 判活（>tolerance 才 stale）
  - `last_busy_since` 空 → 沿用 D1 既有 idle 语义
- `server/config.py` 新增 `expected_remind_runtime_minutes: int = 30`（env MAP_EXPECTED_REMIND_RUNTIME_MINUTES 可覆盖）

### 设计要点

- **busy 容忍阈值** = `max(expected_remind_runtime, 2 × idle_stale_threshold)`
  - 默认 = max(30, 2×15) = 30 分钟
  - 包住正常 claude runtime 调用 + 抖动余量
- busy 行 + stale=True 的语义：busy 太久（>30min）= 真卡死，应该显示 stale WARN
- busy 行 + stale=False 的语义：busy 短时间内 = 正常，**不显示** stale WARN（避免误导）
- idle 行 + stale=True：旧逻辑保持不变（last_waker_poll_at 超 threshold）

### 验证

- `ruff check` 全绿
- e2e (in-process SessionLocal + 真 DB) 4 个 case 全过：
  - idle + old poll (2h ago) → `stale=True`
  - idle + fresh poll (1min ago) → `stale=False`
  - busy 5min ago + poll 2h ago → `stale=False`（busy 不算 stale）
  - busy 2h ago (>30min tolerance) → `stale=True`

下一节: I4 — `cli/waker_heartbeat_render.py` 渲染 busy 字段 + busy 行不输出 stale WARN + busy > 2h 软警告
