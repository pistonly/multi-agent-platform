// T37: notifications API + SSE stream, extracted from client.ts. The
// reconnect state machine (Last-Event-ID replay + exponential backoff with
// jitter) was the only stateful block in an otherwise flat CRUD file — it
// belongs in its own module, next to the notification endpoints it serves.
import { api } from "./client";
import type { NotificationList, NotificationStreamEvent } from "./types";

export async function fetchNotifications(params?: {
  unread_only?: boolean;
  category?: "wakeable" | "digest" | "all";
  target_type?: string;
  limit?: number;
  offset?: number;
}): Promise<NotificationList> {
  const { data } = await api.get<NotificationList>("/agents/me/notifications", { params });
  return data;
}

const SSE_INITIAL_RETRY_MS = 1000;
const SSE_MAX_RETRY_MS = 30_000;
// 致命状态码：token 失效，重连只会再次失败 → 停止重连（其他 axios 请求的
// 401/403 会被 AuthContext 拦截器处理登出；SSE 这边停止即可，避免死循环）。
const SSE_FATAL_STATUS = new Set([401, 403]);

export interface NotificationStreamError {
  /** HTTP 状态码（连接建立失败时）。 */
  status?: number;
  /** true = 不应再重连（401/403）。 */
  fatal: boolean;
}

export async function streamNotifications(
  token: string,
  opts: {
    signal: AbortSignal;
    onEvent: (event: NotificationStreamEvent) => void;
    onError?: (error: NotificationStreamError) => void;
  }
): Promise<void> {
  const baseURL = api.defaults.baseURL ?? "";
  const url = `${baseURL}/agents/me/notifications/stream`;
  // 最后收到的 event id。重连时通过 Last-Event-ID 头带回，服务端从 ring
  // buffer 回放断线期间丢失的事件（P2 #9 服务端已支持，这里是客户端半边）。
  let lastEventId = 0;
  let attempt = 0;

  while (!opts.signal.aborted) {
    try {
      const headers: Record<string, string> = { Authorization: `Bearer ${token}` };
      if (lastEventId > 0) headers["Last-Event-ID"] = String(lastEventId);
      const response = await fetch(url, { headers, signal: opts.signal });

      if (!response.ok || !response.body) {
        if (SSE_FATAL_STATUS.has(response.status)) {
          opts.onError?.({ status: response.status, fatal: true });
          return;
        }
        throw new Error(`SSE failed: ${response.status}`);
      }

      // 连接建立成功，重置退避计数。
      attempt = 0;

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (!opts.signal.aborted) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const events = buffer.split("\n\n");
        buffer = events.pop() ?? "";
        for (const eventBlock of events) {
          // 一个 event block 含 id:/data:/event:/retry:/心跳(:) 多行。
          // 后端帧顺序是 id 在 data 前；仅在 data 成功解析后再推进 lastEventId，
          // 这样坏数据不会卡住重连（重连会从上一个好 id 重放）。
          let dataLine: string | null = null;
          let pendingId: number | null = null;
          for (const line of eventBlock.split("\n")) {
            if (line.startsWith("id: ")) {
              pendingId = Number(line.slice(4));
            } else if (line.startsWith("data: ")) {
              dataLine = line.slice(6);
            }
            // event: / retry: / 心跳注释行（`: heartbeat`）忽略。
          }
          if (dataLine === null) continue;
          try {
            opts.onEvent(JSON.parse(dataLine) as NotificationStreamEvent);
          } catch {
            continue; // 跳过无法解析的事件，不更新 lastEventId（重连会重放它）。
          }
          if (pendingId !== null && Number.isFinite(pendingId)) {
            lastEventId = Math.max(lastEventId, pendingId);
          }
        }
      }
    } catch {
      if (opts.signal.aborted) return;
      opts.onError?.({ fatal: false });
      // 指数退避 + jitter：1s→2s→4s…→30s 封顶，加随机抖动避免所有客户端
      // 同步重连（thundering herd）。
      const base = Math.min(SSE_INITIAL_RETRY_MS * 2 ** attempt, SSE_MAX_RETRY_MS);
      const delay = base / 2 + Math.random() * (base / 2);
      attempt += 1;
      await new Promise((resolve) => setTimeout(resolve, delay));
    }
  }
}

export async function markNotificationRead(notificationId: string): Promise<void> {
  await api.post(`/notifications/${notificationId}/read`);
}

export async function markAllNotificationsRead(): Promise<{ marked: number }> {
  const { data } = await api.post<{ marked: number }>("/agents/me/notifications/read-all");
  return data;
}
