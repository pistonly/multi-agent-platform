import { afterEach, describe, expect, it, vi } from "vitest";
import { api, fetchProjectExperiments, fetchTopics, parseTotalCount } from "./client";

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
});
