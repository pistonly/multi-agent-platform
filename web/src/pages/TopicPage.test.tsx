// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { TopicRead } from "../api/types";
import { TopicPage } from "./TopicPage";

const mocks = vi.hoisted(() => ({
  fetchTopic: vi.fn(),
  markTopicRead: vi.fn(),
}));

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return {
    ...actual,
    fetchTopic: mocks.fetchTopic,
    markTopicRead: mocks.markTopicRead,
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
  mocks.fetchTopic.mockReset();
  mocks.markTopicRead.mockReset();
});

const topicFixture: TopicRead = {
  id: "topic-1",
  project_id: "project-1",
  creator_agent_id: "agent-host",
  creator_name: "host",
  title: "Topic read cursor",
  description: "description",
  status: "open",
  pinned: false,
  discussion_round: "round1",
  round_summary_count: 0,
  comment_count: 0,
  experiment_count: 0,
  last_comment_id: null,
  last_comment_author_agent_id: null,
  last_comment_author_name: null,
  last_comment_excerpt: null,
  my_comment_count: 0,
  dismissed_at: null,
  advance_round_pending_since: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  archived_at: null,
  experiments: [],
  comments: [],
  decision: null,
  content_source: "db",
};

function renderTopicPage() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/topics/topic-1"]}>
        <Routes>
          <Route path="/topics/:topicId" element={<TopicPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("TopicPage", () => {
  it("marks contextual unread as seen when the topic detail loads", async () => {
    mocks.fetchTopic.mockResolvedValue(topicFixture);

    renderTopicPage();

    expect(await screen.findByText("Topic read cursor")).toBeTruthy();
    await waitFor(() => expect(mocks.markTopicRead).toHaveBeenCalledWith("topic-1"));
  });

  it("skips mark-read for FS topics (no DB read cursor, backend would 404)", async () => {
    mocks.fetchTopic.mockResolvedValue({ ...topicFixture, content_source: "fs-local" });

    renderTopicPage();

    expect(await screen.findByText("Topic read cursor")).toBeTruthy();
    // 等待一拍，确认 effect 已跑完且没有触发 markTopicRead
    await waitFor(() => expect(screen.getByText("Topic read cursor")).toBeTruthy());
    expect(mocks.markTopicRead).not.toHaveBeenCalled();
  });

  it("renders remote FS projection as read-only", async () => {
    mocks.fetchTopic.mockResolvedValue({
      ...topicFixture,
      content_source: "fs-projection",
      source: {
        content_source: "fs-projection",
        source_revision: "3",
        source_updated_at: "2026-08-21T01:00:00Z",
        stale: true,
        stale_reason: "freshness_sla_exceeded",
      },
    });

    renderTopicPage();

    expect(await screen.findByText(/远程 FS 投影的只读视图/)).toBeTruthy();
    expect(await screen.findByText(/revision 3/)).toBeTruthy();
    expect(screen.getByText(/最后同步于/)).toBeTruthy();
    expect(screen.getByText(/内容可能陈旧/)).toBeTruthy();
    expect(screen.queryByPlaceholderText(/参与讨论/)).toBeNull();
    expect(screen.queryByText("关闭话题")).toBeNull();
    expect(screen.queryByText("沉淀结论")).toBeNull();
    expect(screen.getByText(/话题评论、关闭、结论和归档请用 CLI/)).toBeTruthy();
  });

  it("hides retired DB write controls on fs-local topics and shows CLI commands", async () => {
    mocks.fetchTopic.mockResolvedValue({
      ...topicFixture,
      content_source: "fs-local",
      slug: "cli-guide",
    });

    renderTopicPage();

    expect(await screen.findByText(/话题评论、关闭、结论和归档请用 CLI/)).toBeTruthy();
    expect(screen.getByText(/map --persona host fs comment --topic cli-guide/)).toBeTruthy();
    expect(screen.queryByText("关闭话题")).toBeNull();
    expect(screen.queryByPlaceholderText(/参与讨论/)).toBeNull();
  });

  it("points leftover DB topics at migrate instead of 410 write buttons", async () => {
    mocks.fetchTopic.mockResolvedValue(topicFixture);

    renderTopicPage();

    expect(await screen.findByText(/存量 DB 话题/)).toBeTruthy();
    expect(screen.getByText(/map --persona host topic migrate --id topic-1/)).toBeTruthy();
    expect(screen.queryByText("关闭话题")).toBeNull();
    expect(screen.queryByPlaceholderText(/参与讨论/)).toBeNull();
  });
});
