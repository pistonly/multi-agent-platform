import { describe, it, expect } from "vitest";
import type { TodoRead } from "../api/types";
import { sumTodos } from "./todoCount";

function emptyTodos(): TodoRead {
  return {
    my_open_experiments: [],
    pending_reviews: [],
    pending_result_reviews: [],
    pending_replies: [],
    pending_plan_revisions: [],
    pending_topic_replies: [],
    pending_round_acks: [],
    pending_advance_rounds: [],
    stale_open_topics: [],
    my_open_topics: [],
    mentions: [],
    action_items: [],
  };
}

describe("sumTodos", () => {
  it("returns 0 when every bucket is empty", () => {
    expect(sumTodos(emptyTodos())).toBe(0);
  });

  it("sums every bucket's length", () => {
    const data = emptyTodos();
    data.mentions = [{}, {}, {}] as never;
    data.pending_reviews = [{}] as never;
    data.action_items = [{}, {}] as never;
    expect(sumTodos(data)).toBe(6);
  });

  it("counts obligation buckets only (passive my_open_* excluded)", () => {
    const data: TodoRead = {
      my_open_experiments: [{}] as never,
      pending_reviews: [{}] as never,
      pending_result_reviews: [{}] as never,
      pending_replies: [{}] as never,
      pending_plan_revisions: [{}] as never,
      pending_topic_replies: [{}] as never,
      pending_round_acks: [{}] as never,
      pending_advance_rounds: [{}] as never,
      stale_open_topics: [{}] as never,
      my_open_topics: [{}] as never,
      mentions: [{}] as never,
      action_items: [{}] as never,
    };
    expect(sumTodos(data)).toBe(10);
  });
});
