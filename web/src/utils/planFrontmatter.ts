/** YAML frontmatter required by create_experiment / plan_revise (a764abf6). */

export const PLAN_FRONTMATTER_TEMPLATE = `---
title: "实验标题"
acceptance:
  - "可验证的验收标准（每条一句）"
evidence_keys:
  - "pytest_summary"
dependencies: []
---

## 目标
`;

const FRONTMATTER_RE = /^\s*---\s*\n/;

export function hasPlanFrontmatter(content: string): boolean {
  return FRONTMATTER_RE.test(content);
}

export function wrapPlanWithFrontmatter(plan: string, title: string): string {
  const trimmed = plan.trim();
  if (hasPlanFrontmatter(trimmed)) return trimmed;
  const safeTitle = title.trim() || "实验标题";
  return `---
title: ${JSON.stringify(safeTitle)}
acceptance:
  - "可验证的验收标准（每条一句）"
evidence_keys:
  - "pytest_summary"
dependencies: []
---

${trimmed}
`;
}
