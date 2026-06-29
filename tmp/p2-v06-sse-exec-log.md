# v0.6 P2 SSE 执行日志

## 变更

| 层 | 内容 |
|----|------|
| `server/services/notification_stream.py` | pub/sub + SSE generator |
| `server/services/notification_service.py` | 创建通知后 publish |
| `server/api/agents.py` | `GET /me/notifications/stream` |
| `web/src/api/client.ts` | `streamNotifications` |
| `web/src/hooks/useNotificationStream.ts` | invalidate 通知/待办 |
| `web/src/components/Layout.tsx` | 挂载 SSE，轮询 120s |

## 验收

- [x] Bearer 鉴权 SSE
- [x] submit-review → reviewer 收到 stream 事件
- [x] pytest 5 + stream 2；web build OK
