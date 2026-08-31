## I6 — commit + complete experiment

### Commit

- `fb1c520 map exp b3ec2e4d: waker busy/dead 拆分 — busy 状态机 + 容忍阈值 + 渲染（I1-I5）`
- 17 files changed, 688 insertions(+), 11 deletions(-)
- 窄白名单：`cli/`、`sdk/`、`server/`、`tests/`（plan A8 边界）

### 验收

| 验收项 | 结果 |
|--------|------|
| A1 忙心跳写入与清理 | ✓ `_touch_busy` / `_clear_busy` 写 state 三字段 + PATCH server |
| A3 busy vs stale 区分 | ✓ busy 不算 stale，busy > 容忍阈值才 stale |
| A5 map work 渲染 | ✓ `last_busy_since` 字段 + busy 行不显示 stale WARN + busy > 2h 软警告 |
| A6 回归测试 | ✓ 9 个新 case (5 simple_waker + 4 status_service) 全过 |
| A7 ruff check 全绿 | ✓ |
| A7 pytest 全绿 | ✓ 1773 passed, 0 failed |
| A8 边界 | ✓ 不引入新 wake signature / work_items kind，不改 remind 冷却/退避语义 |

### 实测输出（busy > 2h）

```
$ map --persona host agent heartbeat --busy-since "2026-08-30T15:00:00+00:00"
agent_id: 8ab78cfc-8289-442e-ae63-fa0df4d2cd68
busy_since: '2026-08-30T15:00:00Z'

$ map --persona host work 2>&1 1>/dev/null
## waker 心跳
multi-agents-platform-host (host) last_waker_poll_at=2026-08-30T19:54:28.885585+00:00 last_busy_since=2026-08-30T15:00:00+00:00 busy
[WARN] busy > 2h for multi-agents-platform-host(host) (5:17:27.171024)
[HINT] busy 超过 2h 视为真卡死：ps 查 waker / runtime 子进程；或重启对应 waker（_check_busy_crash_recovery 会清 server 列）
```

busy_since 清零后 `map work` 回归正常轮询心跳，无 busy 警告。

### grep 核证

```
cli/simple_waker.py:1062:    def _touch_busy(self, stats: SimpleWakerStats, *, now: datetime) -> None:
cli/simple_waker.py:1077:    def _clear_busy(self, stats: SimpleWakerStats) -> None:
cli/simple_waker.py:1097:    def _check_busy_crash_recovery(self) -> None:
server/services/status_service.py:75:    - ``last_busy_since`` 非空 → busy。
server/services/status_service.py:102:        busy_since = as_utc(agent.last_busy_since)
server/services/status_service.py:116:                last_busy_since=busy_since,
server/domain/models.py: last_busy_since: Mapped[datetime | None]
```

### Release

迁移已升级至 053 (alembic head)。server_daemon 已重启加载新代码（含 `/api/v1/agents/me/heartbeat` endpoint + `WakerHeartbeatRead.last_busy_since` 字段）。
