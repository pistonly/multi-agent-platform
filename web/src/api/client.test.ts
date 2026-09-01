import { afterEach, describe, expect, it, vi } from "vitest";
import { type AxiosError } from "axios";
import {
  api,
  fetchAgents,
  fetchProjectActionItems,
  fetchProjectDecisions,
  fetchProjectExperiments,
  fetchTopics,
  formatApiError,
  markTopicRead,
  parseTotalCount,
} from "./client";

describe("formatApiError", () => {
  it("appends the M55 hint so plan-frontmatter 422s are actionable", () => {
    const error = {
      isAxiosError: true,
      message: "Request failed with status code 422",
      response: {
        data: {
          detail: "Plan frontmatter is missing",
          error_code: "STATE_MACHINE_PLAN_MARKER_MISSING",
          hint: 'Plan must begin with a YAML frontmatter block. Copy this template:\n---\ntitle: "实验标题"\n---',
        },
        status: 422,
      },
    } as unknown as AxiosError<{ detail?: unknown; hint?: string }>;

    expect(formatApiError(error)).toContain("Plan frontmatter is missing");
    expect(formatApiError(error)).toContain('title: "实验标题"');
  });

  it("falls back to detail when hint is absent", () => {
    const error = {
      isAxiosError: true,
      message: "Request failed with status code 400",
      response: { data: { detail: "bad request" }, status: 400 },
    } as unknown as AxiosError<{ detail?: unknown; hint?: string }>;

    expect(formatApiError(error)).toBe("bad request");
  });
});

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


