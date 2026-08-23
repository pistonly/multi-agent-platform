import { describe, expect, it } from "vitest";

describe("lazy route modules", () => {
  it("resolves ExperimentPage chunk export", async () => {
    const mod = await import("./pages/ExperimentPage");
    expect(mod.ExperimentPage).toBeTypeOf("function");
  });

  it("resolves TopicPage chunk export", async () => {
    const mod = await import("./pages/TopicPage");
    expect(mod.TopicPage).toBeTypeOf("function");
  });

  it("resolves ProjectExperimentsPage chunk export", async () => {
    const mod = await import("./pages/ProjectExperimentsPage");
    expect(mod.ProjectExperimentsPage).toBeTypeOf("function");
  });

  it("resolves NotificationsPage chunk export", async () => {
    const mod = await import("./pages/NotificationsPage");
    expect(mod.NotificationsPage).toBeTypeOf("function");
  });
});
