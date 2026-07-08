// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ReviewSummary } from "./ReviewSummary";
import type { Review } from "../api/types";

afterEach(() => cleanup());

function makeReview(overrides: Partial<Review>): Review {
  return {
    id: overrides.id ?? "review-1",
    experiment_id: "exp-1",
    reviewer_agent_id: "agent-reviewer",
    plan_version: 1,
    substitute_kind: "none",
    created_at: "2026-07-01T00:00:00Z",
    items: [],
    ...overrides,
  };
}

function makeReasonable(content: string, id = `ok-${content}`) {
  return {
    id,
    review_id: "review-1",
    kind: "reasonable" as const,
    content,
    status: null,
    created_at: "2026-07-01T00:00:00Z",
    updated_at: "2026-07-01T00:00:00Z",
  };
}

function makeUnreasonable(content: string, id = `bad-${content}`, status: "open" | "addressed" | "rebutted" | "resolved" | "withdrawn" | "escalated" = "open") {
  return {
    id,
    review_id: "review-1",
    kind: "unreasonable" as const,
    content,
    status,
    created_at: "2026-07-01T00:00:00Z",
    updated_at: "2026-07-01T00:00:00Z",
  };
}

function renderReviewSummary(props: { reviews: Review[]; canAddReview?: boolean }) {
  const queryClient = new QueryClient({
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ReviewSummary
        experimentId="exp-1"
        reviews={props.reviews}
        onUpdated={vi.fn()}
        canAddReview={props.canAddReview ?? false}
      />
    </QueryClientProvider>,
  );
}

describe("ReviewSummary — archived reviews (I1(e))", () => {
  it("renders active reviews inline and ignores archived in the summary counts", () => {
    const reviews: Review[] = [
      makeReview({
        id: "active-1",
        plan_version: 2,
        items: [makeReasonable("目标清晰", "ok-1"), makeUnreasonable("缺少验收", "bad-1", "open")],
      }),
      makeReview({
        id: "archived-1",
        plan_version: 1,
        archived_at: "2026-07-01T12:00:00Z",
        archived_reason: "auto",
        items: [makeReasonable("v1 ok", "ok-2"), makeUnreasonable("v1 bad", "bad-2", "open")],
      }),
    ];

    renderReviewSummary({ reviews });

    // Active review items appear in the main list.
    expect(screen.getByText("缺少验收")).toBeDefined();
    expect(screen.getAllByText("目标清晰").length).toBeGreaterThanOrEqual(1);

    // Archived section is rendered with toggle button + count.
    const toggle = screen.getByTestId("toggle-archived-reviews");
    expect(toggle).toBeDefined();
    expect(toggle.textContent).toContain("已归档评审 (1)");
    expect(toggle.getAttribute("aria-expanded")).toBe("false");

    // Archived item not in main list by default.
    expect(screen.queryByText("v1 bad")).toBeNull();
  });

  it("expands the archived section when toggle is clicked and shows archived items", () => {
    const reviews: Review[] = [
      makeReview({
        id: "archived-1",
        plan_version: 1,
        archived_at: "2026-07-01T12:00:00Z",
        archived_reason: "auto",
        items: [makeUnreasonable("v1 stale item", "bad-1", "open")],
      }),
    ];

    renderReviewSummary({ reviews });

    const toggle = screen.getByTestId("toggle-archived-reviews");
    fireEvent.click(toggle);

    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByTestId("archived-reviews-list")).toBeDefined();
    expect(screen.getByText("v1 stale item")).toBeDefined();

    const card = screen.getByTestId("archived-review-card");
    expect(card.getAttribute("data-archived-reason")).toBe("auto");
    expect(card.textContent).toContain("plan v1");
  });

  it("renders 'pre-archive, all reviews shown' hint for migration-034 marker rows (archived_reason=auto + archived_at=null)", () => {
    const reviews: Review[] = [
      makeReview({
        id: "marker-1",
        plan_version: 1,
        archived_at: null,
        archived_reason: "auto",
        items: [makeReasonable("historical", "ok-h")],
      }),
    ];

    renderReviewSummary({ reviews });

    const toggle = screen.getByTestId("toggle-archived-reviews");
    fireEvent.click(toggle);

    const card = screen.getByTestId("archived-review-card");
    expect(card.textContent).toContain("pre-archive, all reviews shown");
  });

  it("does not render the archived section when there are no archived reviews", () => {
    const reviews: Review[] = [
      makeReview({ id: "active-1", plan_version: 2, items: [makeReasonable("ok", "ok-1")] }),
    ];

    renderReviewSummary({ reviews });

    expect(screen.queryByTestId("archived-reviews-section")).toBeNull();
  });

  it("supports multiple archived reviews (plan_revise finalize chained)", () => {
    const reviews: Review[] = [
      makeReview({
        id: "archived-v1",
        plan_version: 1,
        archived_at: "2026-07-01T10:00:00Z",
        archived_reason: "auto",
        items: [makeReasonable("v1 ok", "ok-v1")],
      }),
      makeReview({
        id: "archived-v2",
        plan_version: 2,
        archived_at: "2026-07-01T11:00:00Z",
        archived_reason: "auto",
        items: [makeReasonable("v2 ok", "ok-v2")],
      }),
      makeReview({
        id: "active-v3",
        plan_version: 3,
        items: [makeReasonable("v3 ok", "ok-v3")],
      }),
    ];

    renderReviewSummary({ reviews });

    const toggle = screen.getByTestId("toggle-archived-reviews");
    expect(toggle.textContent).toContain("已归档评审 (2)");

    fireEvent.click(toggle);
    const cards = screen.getAllByTestId("archived-review-card");
    expect(cards).toHaveLength(2);
  });
});
