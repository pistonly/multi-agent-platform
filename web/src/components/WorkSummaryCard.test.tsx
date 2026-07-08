// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { WorkSummaryCard } from "./WorkSummaryCard";
import type { AgentWorkSummary, SummaryBucket } from "../api/types";

afterEach(() => cleanup());

function makeBucket(
  kind: SummaryBucket["kind"],
  count: number,
  overrides: Partial<SummaryBucket> = {},
): SummaryBucket {
  return {
    kind,
    count,
    visibility: "all",
    partition_visibility: "all",
    items: [],
    top_excerpt: null,
    ...overrides,
  };
}

const baseAgent = {
  id: "agent-1",
  name: "host",
  role: "agent" as const,
  project_id: null,
  project_key: null,
  created_at: "2026-07-01T00:00:00Z",
};

describe("WorkSummaryCard", () => {
  it("renders all 6 bucket kinds in fixed order", () => {
    const summary: AgentWorkSummary = {
      agent: baseAgent,
      buckets: [
        makeBucket("action_items", 0),
        makeBucket("mention", 0),
        makeBucket("pending_reply", 0),
        makeBucket("round_ack", 0),
        makeBucket("explicit_only", 0),
        makeBucket("informational_only", 0),
      ],
      topics_needing_attention: 0,
      experiments_needing_attention: 0,
      topics_truncated: 0,
      experiments_truncated: 0,
      visibility_filter_applied: false,
      topics_limit: 10,
      experiments_limit: 5,
    };
    const { container } = render(<WorkSummaryCard summary={summary} />);
    const buckets = container.querySelectorAll(
      '[data-testid^="work-summary-bucket-"]',
    );
    const kinds = Array.from(buckets).map((el) =>
      (el.getAttribute("data-testid") ?? "").replace(
        "work-summary-bucket-",
        "",
      ),
    );
    expect(kinds).toEqual([
      "mention",
      "round_ack",
      "pending_reply",
      "explicit_only",
      "informational_only",
      "action_items",
    ]);
  });

  it("renders bucket count, visibility label, and item rows", () => {
    const summary: AgentWorkSummary = {
      agent: baseAgent,
      buckets: [
        makeBucket("mention", 2, {
          visibility: "all",
          top_excerpt: "review this design",
          items: [
            {
              kind: "mention",
              topic_id: "topic-1",
              topic_title: "design discussion",
              excerpt: "@host",
              updated_at: "2026-07-08T01:00:00Z",
            },
          ],
        }),
        makeBucket("explicit_only", 1, {
          visibility: "host_only",
        }),
      ],
      topics_needing_attention: 1,
      experiments_needing_attention: 1,
      topics_truncated: 0,
      experiments_truncated: 0,
      visibility_filter_applied: false,
      topics_limit: 10,
      experiments_limit: 5,
    };
    render(<WorkSummaryCard summary={summary} />);
    expect(screen.getByTestId("work-summary-bucket-mention")).toBeTruthy();
    expect(screen.getByTestId("work-summary-bucket-explicit_only")).toBeTruthy();
    expect(screen.getAllByTestId("work-summary-bucket-item")).toHaveLength(1);
    expect(screen.getByText("design discussion")).toBeTruthy();
    expect(screen.getByText("全员可见")).toBeTruthy();
    expect(screen.getByText("仅 host")).toBeTruthy();
  });

  it("shows persona-filtered and truncation banners when applicable", () => {
    const summary: AgentWorkSummary = {
      agent: baseAgent,
      buckets: [makeBucket("mention", 0)],
      topics_needing_attention: 0,
      experiments_needing_attention: 0,
      topics_truncated: 3,
      experiments_truncated: 1,
      visibility_filter_applied: true,
      topics_limit: 10,
      experiments_limit: 5,
    };
    render(<WorkSummaryCard summary={summary} />);
    expect(screen.getByTestId("work-summary-persona-filtered")).toBeTruthy();
    const truncated = screen.getByTestId("work-summary-truncated");
    expect(truncated.textContent).toContain("+4");
  });

  it("renders empty-state message when no buckets are present", () => {
    const summary: AgentWorkSummary = {
      agent: baseAgent,
      buckets: [],
      topics_needing_attention: 0,
      experiments_needing_attention: 0,
      topics_truncated: 0,
      experiments_truncated: 0,
      visibility_filter_applied: false,
      topics_limit: 10,
      experiments_limit: 5,
    };
    render(<WorkSummaryCard summary={summary} />);
    expect(screen.getByTestId("work-summary-empty")).toBeTruthy();
  });

  it("renders topics_needing_attention and experiments_needing_attention counters", () => {
    const summary: AgentWorkSummary = {
      agent: baseAgent,
      buckets: [],
      topics_needing_attention: 8,
      experiments_needing_attention: 3,
      topics_truncated: 0,
      experiments_truncated: 0,
      visibility_filter_applied: false,
      topics_limit: 10,
      experiments_limit: 5,
    };
    const { container } = render(<WorkSummaryCard summary={summary} />);
    expect(container.textContent).toContain("话题 8");
    expect(container.textContent).toContain("实验 3");
  });
});
