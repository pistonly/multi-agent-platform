import { afterEach, describe, expect, it, vi } from "vitest";
import {
  api,
  fetchAgents,
  fetchFeedbacks,
  fetchProjectActionItems,
  fetchProjectDecisions,
  fetchProjectExperiments,
  fetchTopics,
  parseTotalCount,
  submitFeedback,
} from "./client";

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

describe("feedback fetchers", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("fetchFeedbacks sends filters and parses total", async () => {
    const get = vi.spyOn(api, "get").mockResolvedValue({
      data: [{ id: "f1" }],
      headers: { "x-total-count": "9" },
      status: 200,
      statusText: "OK",
      config: {} as never,
    });

    const result = await fetchFeedbacks({ status: "new", category: "bug", pageSize: 25 });

    expect(get).toHaveBeenCalledWith("/feedback", {
      params: {
        status: "new",
        category: "bug",
        page: 1,
        page_size: 25,
        include_archived: false,
      },
    });
    expect(result).toEqual({ items: [{ id: "f1" }], total: 9 });
  });

  it("submitFeedback posts body and category", async () => {
    const post = vi.spyOn(api, "post").mockResolvedValue({
      data: { id: "f2", body: "hi" },
      status: 201,
      statusText: "Created",
      headers: {},
      config: {} as never,
    });

    const result = await submitFeedback({ body: "hi", category: "suggestion" });

    expect(post).toHaveBeenCalledWith("/feedback", { body: "hi", category: "suggestion" });
    expect(result).toEqual({ id: "f2", body: "hi" });
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
