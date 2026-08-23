import { afterEach, describe, expect, it, vi } from "vitest";
import {
  api,
  fetchAgents,
  fetchProjectActionItems,
  fetchProjectDecisions,
  fetchProjectExperiments,
  fetchTopics,
  markTopicRead,
  parseTotalCount,
  streamNotifications,
} from "./client";
import type { NotificationStreamError } from "./client";

describe("parseTotalCount", () => {
  it("reads lowercase x-total-count header", () => {
    expect(parseTotalCount({ "x-total-count": "42" })).toBe(42);
  });

  it("reads X-Total-Count header", () => {
    expect(parseTotalCount({ "X-Total-Count": "7" })).toBe(7);
  });

  it("returns 0 for missing or invalid header", () => {
    expect(parseTotalCount({})).toBe(0);
    expect(parseTotalCount({ "x-total-count": "n/a" })).toBe(0);
  });
});

describe("paginated list fetchers", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("fetchTopics sends pagination params and parses total", async () => {
    const get = vi.spyOn(api, "get").mockResolvedValue({
      data: [{ id: "t1" }],
      headers: { "x-total-count": "15" },
      status: 200,
      statusText: "OK",
      config: {} as never,
    });

    const result = await fetchTopics("proj-1", {
      status: "open",
      q: "foo",
      page: 2,
      pageSize: 10,
      includeArchived: true,
    });

    expect(get).toHaveBeenCalledWith("/projects/proj-1/topics", {
      params: {
        status: "open",
        q: "foo",
        page: 2,
        page_size: 10,
        include_archived: true,
      },
    });
    expect(result).toEqual({ items: [{ id: "t1" }], total: 15 });
  });

  it("markTopicRead posts to the per-agent topic read cursor endpoint", async () => {
    const post = vi.spyOn(api, "post").mockResolvedValue({
      data: {},
      headers: {},
      status: 200,
      statusText: "OK",
      config: {} as never,
    });

    await markTopicRead("topic-1");

    expect(post).toHaveBeenCalledWith("/agents/me/topics/topic-1/read");
  });

  it("fetchProjectExperiments sends phase filter and total", async () => {
    const get = vi.spyOn(api, "get").mockResolvedValue({
      data: [],
      headers: { "X-Total-Count": "3" },
      status: 200,
      statusText: "OK",
      config: {} as never,
    });

    const result = await fetchProjectExperiments("proj-2", { phase: "done", page: 1 });

    expect(get).toHaveBeenCalledWith("/projects/proj-2/experiments", {
      params: {
        phase: "done",
        page: 1,
        page_size: 20,
        include_archived: false,
      },
    });
    expect(result).toEqual({ items: [], total: 3 });
  });

  it("fetchProjectDecisions sends limit", async () => {
    const get = vi.spyOn(api, "get").mockResolvedValue({
      data: [{ id: "d1" }],
      headers: {},
      status: 200,
      statusText: "OK",
      config: {} as never,
    });

    const result = await fetchProjectDecisions("proj-3", 5);

    expect(get).toHaveBeenCalledWith("/projects/proj-3/decisions", {
      params: { limit: 5 },
    });
    expect(result).toEqual([{ id: "d1" }]);
  });

  it("fetchProjectActionItems sends owner and status filters", async () => {
    const get = vi.spyOn(api, "get").mockResolvedValue({
      data: [],
      headers: {},
      status: 200,
      statusText: "OK",
      config: {} as never,
    });

    const result = await fetchProjectActionItems({
      projectId: "proj-4",
      ownerAgentId: "agent-1",
      status: "open",
      limit: 10,
    });

    expect(get).toHaveBeenCalledWith("/projects/proj-4/action-items", {
      params: {
        owner_agent_id: "agent-1",
        status: "open",
        limit: 10,
      },
    });
    expect(result).toEqual([]);
  });
});

describe("agent fetcher", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("fetchAgents sends role and project_id filters", async () => {
    const get = vi.spyOn(api, "get").mockResolvedValue({
      data: [{ id: "a1", name: "host", role: "agent" }],
      headers: {},
      status: 200,
      statusText: "OK",
      config: {} as never,
    });

    const result = await fetchAgents({ role: "agent", projectId: "p1" });

    expect(get).toHaveBeenCalledWith("/agents", {
      params: { role: "agent", project_id: "p1" },
    });
    expect(result).toEqual([{ id: "a1", name: "host", role: "agent" }]);
  });

  it("fetchAgents with no options sends no params", async () => {
    const get = vi.spyOn(api, "get").mockResolvedValue({
      data: [],
      headers: {},
      status: 200,
      statusText: "OK",
      config: {} as never,
    });

    await fetchAgents();

    expect(get).toHaveBeenCalledWith("/agents", { params: {} });
  });
});

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
