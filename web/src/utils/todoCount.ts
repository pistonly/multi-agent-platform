import type { TodoRead } from "../api/types";

/**
 * Sum the lengths of every todo bucket in TodoRead.
 *
 * Single source of truth shared by the nav badge (Layout) and the empty-state
 * check (TodosPage) so both track the same definition of "未处理待办" as the
 * Web 待办 page. Bucket list mirrors TodoRead field-for-field.
 */
export function sumTodos(data: TodoRead): number {
  return (
    (data.pending_plan_revisions ?? []).length +
    (data.pending_reviews ?? []).length +
    (data.pending_result_reviews ?? []).length +
    (data.pending_replies ?? []).length +
    (data.pending_topic_replies ?? []).length +
    (data.pending_round_acks ?? []).length +
    (data.pending_advance_rounds ?? []).length +
    (data.stale_open_topics ?? []).length +
    (data.mentions ?? []).length +
    (data.action_items ?? []).length
  );
}
