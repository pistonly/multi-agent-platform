import { describe, expect, it } from "vitest";
import {
  blockedOnMessage,
  hasExperimentAction,
  shouldShowBlockedBanner,
} from "./experimentCapabilities";

describe("experimentCapabilities", () => {
  it("hasExperimentAction checks membership", () => {
    expect(hasExperimentAction(["approve", "withdraw"], "approve")).toBe(true);
    expect(hasExperimentAction(["approve"], "start")).toBe(false);
    expect(hasExperimentAction(undefined, "start")).toBe(false);
  });

  it("blockedOnMessage maps known codes and ignores none", () => {
    expect(blockedOnMessage("awaiting_result_approval")).toBe("等待其他 Agent 审批结果");
    expect(blockedOnMessage("awaiting_review_for_current_plan_version")).toBe(
      "计划已修订，等待评审方对当前版本重新提交评审（review add）",
    );
    expect(blockedOnMessage("none")).toBeNull();
    expect(blockedOnMessage(null)).toBeNull();
  });

  it("shouldShowBlockedBanner when blocked with no actions", () => {
    expect(shouldShowBlockedBanner([], "awaiting_non_creator_review")).toBe(true);
    expect(shouldShowBlockedBanner([], "awaiting_review_for_current_plan_version")).toBe(
      true,
    );
    expect(shouldShowBlockedBanner(["plan_revise"], "open_unreasonable_item")).toBe(false);
    expect(shouldShowBlockedBanner([], "none")).toBe(false);
  });
});
