import { describe, expect, it } from "vitest";
import {
  PLAN_FRONTMATTER_TEMPLATE,
  hasPlanFrontmatter,
  wrapPlanWithFrontmatter,
} from "./planFrontmatter";

describe("planFrontmatter", () => {
  it("detects a leading YAML fence", () => {
    expect(hasPlanFrontmatter(PLAN_FRONTMATTER_TEMPLATE)).toBe(true);
    expect(hasPlanFrontmatter("## 目标\n...")).toBe(false);
    expect(hasPlanFrontmatter("")).toBe(false);
  });

  it("leaves an existing frontmatter block unchanged", () => {
    const plan = '---\ntitle: "kept"\n---\n\n## body';
    expect(wrapPlanWithFrontmatter(plan, "ignored")).toBe(plan);
  });

  it("wraps body-only markdown using the form title", () => {
    const wrapped = wrapPlanWithFrontmatter("## test", "Ship the CLI");
    expect(wrapped.startsWith("---\n")).toBe(true);
    expect(wrapped).toContain('title: "Ship the CLI"');
    expect(wrapped).toContain("acceptance:");
    expect(wrapped).toContain("evidence_keys:");
    expect(wrapped).toContain("dependencies: []");
    expect(wrapped).toContain("## test");
  });
});
