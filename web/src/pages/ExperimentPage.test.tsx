// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ExperimentBundleRead } from "../api/types";
import { ExperimentPage } from "./ExperimentPage";

const mocks = vi.hoisted(() => ({
  fetchExperimentBundle: vi.fn(),
}));

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return {
    ...actual,
    fetchExperimentBundle: mocks.fetchExperimentBundle,
  };
});

vi.mock("../context/AuthContext", () => ({
  useAuth: () => ({
    agent: {
      id: "agent-host",
      name: "host",
      role: "agent",
      project_id: "project-1",
      project_key: "project",
      created_at: "2026-01-01T00:00:00Z",
    },
    isAdmin: false,
  }),
}));

afterEach(() => {
  cleanup();
  mocks.fetchExperimentBundle.mockReset();
});

const bundleFixture: ExperimentBundleRead = {
  experiment: {
    id: "exp-1",
    project_id: "project-1",
    creator_agent_id: "agent-host",
    title: "Acceptance experiment",
    description: null,
    phase: "result_review",
    current_plan_version: 1,
    topic_id: null,
    warnings: [],
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    archived_at: null,
    open_unreasonable_count: 0,
    log_count: 1,
    latest_log_summary: "提交结果",
    actions: [],
    blocked_on: "awaiting_result_approval",
    legacy_self_review: false,
    current_plan: {
      id: "plan-1",
      experiment_id: "exp-1",
      version: 1,
      content_md: "- [acceptance_type: smoke] API health smoke passes",
      author_agent_id: "agent-host",
      change_note: null,
      created_at: "2026-01-01T00:00:00Z",
    },
    plan_version_count: 1,
    review_count: 1,
    acceptance_status: [
      {
        id: "acc-smoke",
        description: "API health smoke passes",
        acceptance_type: "smoke",
        evidence_provided: true,
        reviewer_verdict: null,
      },
    ],
  },
  plans: [],
  reviews: [],
  comments: [],
  logs: [],
};

function renderExperimentPage() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/experiments/exp-1"]}>
        <Routes>
          <Route path="/experiments/:experimentId" element={<ExperimentPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("ExperimentPage", () => {
  it("shows acceptance status from experiment detail", async () => {
    mocks.fetchExperimentBundle.mockResolvedValue(bundleFixture);

    renderExperimentPage();

    expect(await screen.findByText("验收状态")).toBeTruthy();
    expect(screen.getByText("smoke")).toBeTruthy();
    expect(screen.getByText("API health smoke passes")).toBeTruthy();
    expect(screen.getByText("已提供")).toBeTruthy();
    expect(screen.getByText("待评审")).toBeTruthy();
  });
});
