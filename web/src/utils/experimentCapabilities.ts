import type { ExperimentBlockedOn } from "../api/types";

export const BLOCKED_ON_LABELS: Record<ExperimentBlockedOn, string | null> = {
  awaiting_non_creator_review: "等待其他 Agent 提交评审",
  awaiting_result_approval: "等待其他 Agent 审批结果",
  open_unreasonable_item: "计划有待处理的不合理项",
  awaiting_addressed_item_ack: "等待评审方确认已修改项",
  none: null,
};

export function hasExperimentAction(actions: string[] | undefined, action: string): boolean {
  return (actions ?? []).includes(action);
}

export function blockedOnMessage(blockedOn: string | null | undefined): string | null {
  if (!blockedOn || blockedOn === "none") return null;
  return BLOCKED_ON_LABELS[blockedOn as ExperimentBlockedOn] ?? blockedOn;
}

export function shouldShowBlockedBanner(
  actions: string[] | undefined,
  blockedOn: string | null | undefined,
): boolean {
  return !!(blockedOn && blockedOn !== "none" && (actions ?? []).length === 0);
}
