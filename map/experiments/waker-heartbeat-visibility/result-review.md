# waker 心跳可见性 — 结果评审（reviewer 审批通过）

## 结论

accept-result：D1–D6 全部落地，acceptance A1–A6 均有可验证证据闭环，无 unreasonable 残留。

## 验收核对

- **S1/S2 双时间戳 + 记录点**：ae588ff / 92a5e5b 已 committed；普通调用只刷 `last_api_seen_at`，`?client=waker` 加刷 `last_waker_poll_at`；server 单测 `test_plain_call_refreshes_only_api_seen` 锁定归属区分（A1/A2）。
- **S3 stale 判定 + /status 暴露**：1128fce；null→never 不 WARN、fresh 不 stale 由 `test_null_never_not_stale` / `test_fresh_heartbeat_not_stale` 锁定；`/status waker_heartbeats[]` per-agent 形状由 `test_status_shape_waker_heartbeats`（in-process TestClient）锁定（A3/A5）。
- **C1/C2 CLI 标记透传 + 渲染**：97ed83d，waker 子进程 `map work --client waker` 透传；`map work` 顶部 WARN 到 stderr、stdout 保持纯净 YAML（C2 渲染）；CLI 单测 `test_work_renders_stale_warn`（A5）。
- **A4 冷启动**：alembic 050 upgrade head 应用到 prod DB，PRAGMA `table_info(agents)` 确认两列存在，重启不丢。
- **T 测试 + 白名单**：a622871，11 新用例入 `_FAST_GATE_MODULES`；fast-gate 603 passed / 0 failed。
- **D6 文档**：831673b + 87fdf43 补 MAP-SIMPLE-WAKER 三章（降级路径/层次/边界）。

## 已知边界（evidence 已注明，不阻塞通过）

- live :18400 server 未重启故其上 /status 暂不含 `waker_heartbeat[]`（editable 需重启载新码）；形状由 in-process 集成测试锁定。
- 只覆盖「waker 挂、server 活」半边；server 挂为显性故障无需心跳兜底。
