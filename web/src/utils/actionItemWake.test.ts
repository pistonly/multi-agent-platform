import { describe, it, expect } from "vitest";
import {
  formatElapsed,
  isStale,
  wakeBadge,
  WAKE_MAX_COUNT_BEFORE_STALE,
  WAKE_REPEAT_DAYS,
  WAKE_STAGE_THRESHOLDS_HOURS,
} from "./actionItemWake";
import type { TopicActionItemTodo } from "../api/types";

function makeItem(overrides: Partial<TopicActionItemTodo> = {}): TopicActionItemTodo {
  return {
    id: "00000000-0000-0000-0000-000000000001",
    decision_id: "00000000-0000-0000-0000-000000000002",
    project_id: "00000000-0000-0000-0000-000000000003",
    topic_id: "00000000-0000-0000-0000-000000000004",
    topic_title: "topic",
    title: "title",
    description: null,
    status: "open",
    due_at: null,
    linked_experiment_id: null,
    wake_count: 0,
    first_open_at: null,
    last_woken_at: null,
    stale_at: null,
    created_at: "2026-07-01T00:00:00Z",
    updated_at: "2026-07-01T00:00:00Z",
    ...overrides,
  };
}

describe("isStale", () => {
  it("returns true when stale_at is set", () => {
    expect(isStale({ stale_at: "2026-07-02T00:00:00Z" } as TopicActionItemTodo)).toBe(
      true,
    );
  });

  it("returns false when stale_at is null", () => {
    expect(isStale({ stale_at: null } as TopicActionItemTodo)).toBe(false);
  });
});

describe("wakeBadge", () => {
  it("shows pre-wake stage when wake_count is 0", () => {
    const badge = wakeBadge(makeItem({ wake_count: 0 }));
    expect(badge.label).toBe("未唤醒");
    expect(badge.className).toContain("slate");
  });

  it("shows stage 1 / 2 badges with blue color", () => {
    expect(wakeBadge(makeItem({ wake_count: 1 })).label).toBe("唤醒 1/4");
    expect(wakeBadge(makeItem({ wake_count: 2 })).label).toBe("唤醒 2/4");
    expect(wakeBadge(makeItem({ wake_count: 1 })).className).toContain("blue");
  });

  it("shows stage 3 / 4 badges with amber color", () => {
    expect(wakeBadge(makeItem({ wake_count: 3 })).label).toBe("唤醒 3/4");
    expect(wakeBadge(makeItem({ wake_count: 4 })).label).toBe("唤醒 4/4");
    expect(wakeBadge(makeItem({ wake_count: 3 })).className).toContain("amber");
  });

  it("overrides stage badges with stale badge when stale_at is set", () => {
    const item = makeItem({
      wake_count: 4,
      stale_at: "2026-07-02T00:00:00Z",
    });
    const badge = wakeBadge(item);
    expect(badge.label).toBe("stale · admin notified");
    expect(badge.className).toContain("red");
  });

  it("falls back to generic badge when wake_count > 4 and not stale", () => {
    const badge = wakeBadge(makeItem({ wake_count: 7 }));
    expect(badge.label).toBe("唤醒 7");
    expect(badge.className).toContain("amber");
  });
});

describe("formatElapsed", () => {
  const now = new Date("2026-07-02T12:00:00Z");

  it("returns placeholder when first_open_at is null", () => {
    expect(formatElapsed(null, now)).toBe("尚未开始计时");
    expect(formatElapsed(undefined, now)).toBe("尚未开始计时");
  });

  it("returns placeholder when first_open_at is invalid", () => {
    expect(formatElapsed("not-a-date", now)).toBe("尚未开始计时");
  });

  it("returns '刚刚开放' for future timestamps", () => {
    expect(formatElapsed("2026-07-02T13:00:00Z", now)).toBe("刚刚开放");
  });

  it("uses minutes for < 1h", () => {
    expect(formatElapsed("2026-07-02T11:30:00Z", now)).toBe("30 分钟");
    expect(formatElapsed("2026-07-02T11:59:30Z", now)).toBe("1 分钟");
  });

  it("uses hours for < 24h", () => {
    expect(formatElapsed("2026-07-01T12:30:00Z", now)).toBe("23 小时");
    expect(formatElapsed("2026-07-02T05:00:00Z", now)).toBe("7 小时");
  });

  it("crosses 24h boundary into days branch", () => {
    expect(formatElapsed("2026-07-01T12:00:00Z", now)).toBe("1 天");
  });

  it("uses days for >= 24h", () => {
    expect(formatElapsed("2026-06-25T12:00:00Z", now)).toBe("7 天");
    expect(formatElapsed("2026-05-02T12:00:00Z", now)).toBe("61 天");
  });

  it("clamps sub-minute to 1 minute", () => {
    expect(formatElapsed("2026-07-02T11:59:59Z", now)).toBe("1 分钟");
  });
});

describe("threshold constants mirror plan §3", () => {
  it("stage thresholds are (1, 24) and (2, 72)", () => {
    expect(WAKE_STAGE_THRESHOLDS_HOURS).toEqual([
      [1, 24],
      [2, 72],
    ]);
  });

  it("repeat interval is 7 days", () => {
    expect(WAKE_REPEAT_DAYS).toBe(7);
  });

  it("stale kicks in at wake_count == 4", () => {
    expect(WAKE_MAX_COUNT_BEFORE_STALE).toBe(4);
  });
});
