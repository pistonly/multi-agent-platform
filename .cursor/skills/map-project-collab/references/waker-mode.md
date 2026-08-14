# Waker 模式（调度层细节）

> 唤醒后的执行顺序、kind→清理分发表、persona 路由已提取到 [wake.md](wake.md)（被唤醒时读那份即可）。本文只保留**调度层**规则：何时唤醒、静音、升级与审计。平台行为变更历史见 CHANGELOG.md。

## waker 模式表

| 模式 | 状态 | 说明 |
|------|------|------|
| **map-simple-waker** | 默认 | 轮询 `GET /agents/me/work`，统一 remind 唤醒 Agent Runtime |
| host-invoker | 默认（补充） | host 经 `host invoke` 同步调用 participant/reviewer，无需 waker 参与 |
| ~~host-worker bridge~~ | 已停用 | `cli/host_worker.py` 保留作 re-export 兼容层，不再启动 |
| ~~legacy runtime-waker~~ | 已停用 | `MAP_USE_LEGACY_WAKER` 不再生效 |

simple-waker 细节：批量提醒（一次处理多项）；State 存 `.map/simple-waker-state-*.json`；审计在 remind 后写聚合 `inbound_event`（fingerprint=`simple-remind:{persona}:{ts}`）；`map work` / `topic progress` / Web 待办页 / wakeable 通知与平台 API 同源，**waker 不维护第二套业务规则**。

## 调度核心规则

- **topic-progress 是真相**：`topic_work_items_for_agent` 的 per-agent 投影（`work_items[]`：obligation + contextual），不是"最后一条评论非己"启发式
- **`my_open_topics` alone 不触发**：作为被动清单存在；host open topic 超 30 分钟无活动会派生 `stale_open_topics` 待办触发复盘
- **action_item 升级**：remind 前扫描 `todos.action_items`，WAKE → `map action mark-wake-sent`，STALE → `map action mark-stale`
- **reviewer 评审优先**：reviewer 有 `pending_reviews`/`pending_result_reviews`/`pending_replies` 时，对 `pending_topic_replies` 静音不提醒（评审超时优先）
- **advance-round 自动通知**：host 调 `advance-round` 后，平台自动为 required participant/reviewer 生成 wakeable 通知，无需手动 @

## Drain Topics 模式（压测/批量复盘）

环境变量 `DRAIN_TOPICS=1` 启动 waker 时，`my_open_topics` 会被全部派生为 `stale_open_topics` 待办，用于离线批量压测 host 复盘逻辑。日常**不要**开启。

## 实验 waker 兼容

`experiment start` 自动 acquire 锁，`complete`/`cancel` 自动释放；`--executor` 委派后执行结果由 executor 提交。实验生命周期调度细节见 [experiment-host](../../experiment-host/SKILL.md)。
