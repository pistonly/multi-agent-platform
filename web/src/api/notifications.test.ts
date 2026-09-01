// T37: SSE tests follow streamNotifications into its own module (they were
// previously at the bottom of client.test.ts). These only stub the global
// fetch — no axios involvement, matching the production code path.
import { afterEach, describe, expect, it, vi } from "vitest";
import { streamNotifications } from "./notifications";
import type { NotificationStreamError } from "./notifications";

describe("streamNotifications (SSE)", () => {
  const encoder = new TextEncoder();

  function sseBody(chunks: string[]): ReadableStream<Uint8Array> {
    return new ReadableStream<Uint8Array>({
      start(controller) {
        for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
        controller.close();
      },
    });
  }

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("parses id and data, ignores heartbeat comments, dispatches the event", async () => {
    const controller = new AbortController();
    const received: { id?: string }[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          sseBody([
            'id: 7\ndata: {"type":"notification.created","id":"n1"}\n\n',
            ": heartbeat\n\n",
          ]),
          { status: 200 },
        ),
      ),
    );

    await streamNotifications("tok", {
      signal: controller.signal,
      onEvent: (e) => {
        received.push(e as { id?: string });
        controller.abort();
      },
    });

    expect(received).toHaveLength(1);
    expect(received[0].id).toBe("n1");
  });

  it("sends Last-Event-ID header on reconnect after receiving an id", async () => {
    const controller = new AbortController();
    let call = 0;
    let reconnectHeader: string | undefined;
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(
        (_url: string, init?: { headers?: Record<string, string> }) => {
          call += 1;
          if (call === 2) {
            reconnectHeader = init?.headers?.["Last-Event-ID"];
            controller.abort();
            return Promise.resolve(new Response(sseBody([]), { status: 200 }));
          }
          return Promise.resolve(
            new Response(sseBody(['id: 3\ndata: {"type":"notification.created"}\n\n']), {
              status: 200,
            }),
          );
        },
      ),
    );

    await streamNotifications("tok", { signal: controller.signal, onEvent: () => {} });

    expect(call).toBeGreaterThanOrEqual(2);
    expect(reconnectHeader).toBe("3");
  });

  it("stops and reports fatal on 401 without reconnecting", async () => {
    const controller = new AbortController();
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 401 }));
    vi.stubGlobal("fetch", fetchMock);
    const errors: NotificationStreamError[] = [];

    await streamNotifications("tok", {
      signal: controller.signal,
      onEvent: () => {},
      onError: (e) => errors.push(e),
    });

    expect(errors).toEqual([{ status: 401, fatal: true }]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("skips unparseable events and still delivers the good one", async () => {
    const controller = new AbortController();
    const received: { id?: string }[] = [];
    let call = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(() => {
        call += 1;
        if (call === 1) {
          return Promise.resolve(
            new Response(
              sseBody([
                "id: 1\ndata: {not valid json}\n\n",
                'id: 2\ndata: {"type":"notification.created","id":"good"}\n\n',
              ]),
              { status: 200 },
            ),
          );
        }
        controller.abort();
        return Promise.resolve(new Response(sseBody([]), { status: 200 }));
      }),
    );

    await streamNotifications("tok", {
      signal: controller.signal,
      onEvent: (e) => received.push(e as { id?: string }),
    });

    expect(received).toHaveLength(1);
    expect(received[0].id).toBe("good");
  });

  it("reconnects after a transient (non-fatal) error", async () => {
    const controller = new AbortController();
    let call = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(() => {
        call += 1;
        if (call === 1) return Promise.reject(new Error("network down"));
        controller.abort();
        return Promise.resolve(new Response(sseBody([]), { status: 200 }));
      }),
    );

    await streamNotifications("tok", { signal: controller.signal, onEvent: () => {} });

    // 第一次失败 → 退避后必须重连（非致命错误不应停止 SSE）。
    expect(call).toBeGreaterThanOrEqual(2);
  });
});
