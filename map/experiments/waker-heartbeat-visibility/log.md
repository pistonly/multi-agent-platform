# waker 心跳可见性 — 执行 log（round 1 完成）

## Summary

waker 停机时平台侧无存活可见性 → 所有等表态话题静默挂起且无告警。Round 2 六条
定稿决议 D1–D6 全部落地：双时间戳（`last_api_seen_at` / `last_waker_poll_at`）
+ server 侧 stale 判定 + `/status` `waker_heartbeats[]` 全 persona 暴露 +
`map work` 顶部渲染（CLI 零判定逻辑）+ 三章文档。

## Evidence

- **S1 迁移+模型**：alembic 050 + `Agent` ORM 两列（nullable timestamp）；prod DB
  `alembic upgrade head` 049→050，PRAGMA 确认 `last_api_seen_at` /
  `last_waker_poll_at` 两列存在（A4 持久化，重启不丢）。
- **S2 记录点**：`GET /agents/me/work` handler 内同事务单列 UPDATE——普通调用只刷
  api_seen；`?client=waker` 加刷 waker_poll_at；不放 middleware（D1）。
- **S3 判定+暴露**：Settings `waker_stale_threshold_minutes`（env
  `MAP_WAKER_STALE_THRESHOLD_MINUTES` 默认 15min，D2）；`build_waker_heartbeats`
  per-agent stale（null → never 不 WARN）；`GlobalStatusRead.waker_heartbeats[]`
  + `WakerHeartbeatRead`（D3）。
- **C1 waker 标记**：simple-waker 轮询子进程 `map work --client waker` 透传 server
  （`MapCommandClient.work()` + `get_agent_work(client=...)`）。
- **C2 渲染**：`map work` 顶部渲染全部 persona waker 状态到 stderr（stdout 保持纯净
  YAML，waker 子进程解析不受扰动）；stale `[WARN] waker heartbeat stale for
  <agent>(<persona>)`，never 只显状态行；`/status` 不可达静默跳过。
- **测试**：server 7 + CLI 4 共 11 用例入 `_FAST_GATE_MODULES` 白名单（之外会被
  pytest 静默 skip）；fast-gate 全量回归通过。

## 让步 / 边界

- live :18400 server 未重启（editable install 需重启才加载新代码），其上 `/status`
  暂不含 `waker_heartbeats[]`；A4 prod DB schema 已 migration 050 head 落两列，
  A5 `/status` 形状由 in-process integration 测试锁定。
- 只覆盖「waker 挂、server 活」半边；server 挂是显性故障无需心跳兜底（D6 边界）。

## 遗留 / v2 候选

- 「从未配置 waker」与「配置了但从未成功轮询」v1 都归 `never`，v2 可演进区分。
- waker 上报自己的轮询周期 / 双档 interval 的 server 侧动态判定列 v2 候选。
