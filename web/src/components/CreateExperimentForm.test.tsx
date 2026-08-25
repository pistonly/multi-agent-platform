// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CreateExperimentForm } from "./CreateExperimentForm";
import { PLAN_FRONTMATTER_TEMPLATE } from "../utils/planFrontmatter";

const mocks = vi.hoisted(() => ({
  createExperiment: vi.fn(),
}));

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return {
    ...actual,
    createExperiment: mocks.createExperiment,
  };
});

afterEach(() => {
  cleanup();
  mocks.createExperiment.mockReset();
});

function renderForm() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <CreateExperimentForm projectId="project-1" />
    </QueryClientProvider>,
  );
}

describe("CreateExperimentForm", () => {
  it("pre-fills a YAML frontmatter template instead of body-only markdown", () => {
    renderForm();
    const textarea = screen.getByLabelText(/实验计划/) as HTMLTextAreaElement;
    expect(textarea.value).toContain("title:");
    expect(textarea.value).toContain("acceptance:");
    expect(textarea.value).toContain("evidence_keys:");
    expect(textarea.value).toContain("dependencies:");
    expect(textarea.value.startsWith("---")).toBe(true);
    expect(screen.getByText(/必须以 YAML frontmatter 开头/)).toBeTruthy();
    expect(textarea.value).toBe(PLAN_FRONTMATTER_TEMPLATE);
  });

  it("wraps body-only plan markdown before calling createExperiment", async () => {
    mocks.createExperiment.mockResolvedValue({ id: "exp-1", title: "Ship the CLI" });
    renderForm();

    fireEvent.change(screen.getByLabelText("标题"), { target: { value: "Ship the CLI" } });
    fireEvent.change(screen.getByLabelText(/实验计划/), { target: { value: "## test" } });
    fireEvent.submit(screen.getByRole("button", { name: "发布实验" }).closest("form")!);

    await waitFor(() => expect(mocks.createExperiment).toHaveBeenCalledTimes(1));
    const payload = mocks.createExperiment.mock.calls[0][1] as { plan: { content_md: string } };
    expect(payload.plan.content_md).toContain('title: "Ship the CLI"');
    expect(payload.plan.content_md).toContain("## test");
    expect(payload.plan.content_md.startsWith("---")).toBe(true);
  });
});
