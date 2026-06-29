# 目标

v0.6 P2：站内通知 **SSE 实时推送**，替代 Web 30s 轮询刷新。

# 范围

## 后端

- `notification_stream` 进程内 pub/sub（按 `agent_id`）
- `GET /api/v1/agents/me/notifications/stream`（`text/event-stream`，25s heartbeat）
- `notification_service` 写入通知后 `publish` `notification.created` 事件

## Web

- `streamNotifications`（fetch + ReadableStream，Bearer 鉴权，断线重连）
- `useNotificationStream`：收到事件 invalidate `notifications` / `todos`
- Layout 挂载 SSE；轮询降为 120s 兜底

# 非目标

- WebSocket、多实例 Redis pub/sub、CLI/SDK 流式 API

# 验收

- [ ] SSE 端点需 Bearer 认证
- [ ] 新通知触发 stream publish + Web invalidate
- [ ] pytest + web build 通过
