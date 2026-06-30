import { describe, expect, it } from "vitest";
import {
  detectMentionTrigger,
  filterMentionCandidates,
  reduceMentionKey,
  replaceMention,
  wrapActiveIndex,
  type MentionCandidate,
} from "./mentionInput";

const agents: MentionCandidate[] = [
  { id: "1", name: "alpha" },
  { id: "2", name: "alpine-bot" },
  { id: "3", name: "Bravo" },
  { id: "4", name: "charlie-1" },
  { id: "5", name: "评审员" },
  { id: "6", name: "delta" },
];

describe("detectMentionTrigger", () => {
  it("returns null for empty caret / out-of-range caret", () => {
    expect(detectMentionTrigger("", 0)).toBeNull();
    expect(detectMentionTrigger("hi", 5)).toBeNull();
  });

  it("detects a bare @ at the start of the input", () => {
    expect(detectMentionTrigger("@", 1)).toEqual({ start: 0, query: "", caret: 1 });
  });

  it("detects @ after whitespace", () => {
    expect(detectMentionTrigger("hello @", 7)).toEqual({ start: 6, query: "", caret: 7 });
  });

  it("captures the query typed after @", () => {
    expect(detectMentionTrigger("hello @al", 9)).toEqual({
      start: 6,
      query: "al",
      caret: 9,
    });
  });

  it("returns null when @ is glued to a non-whitespace character (email)", () => {
    expect(detectMentionTrigger("user@host", 9)).toBeNull();
  });

  it("closes the popover once the user types whitespace after the query", () => {
    // After the whitespace the caret is at index 9 (right after the space).
    expect(detectMentionTrigger("hello @al world", 9)).toBeNull();
    expect(detectMentionTrigger("hello @al world", 13)).toBeNull();
  });

  it("supports Chinese characters in the query (CJK display names)", () => {
    expect(detectMentionTrigger("请看 @评审员", 7)).toEqual({
      start: 3,
      query: "评审员",
      caret: 7,
    });
  });

  it("supports dotted / dashed / underscored query characters", () => {
    expect(detectMentionTrigger("@alpha-bot_v2", 12)).toEqual({
      start: 0,
      query: "alpha-bot_v2",
      caret: 12,
    });
  });

  it("returns null when the caret is to the left of the @", () => {
    expect(detectMentionTrigger("@hello", 0)).toBeNull();
  });

  it("detects @ immediately after a newline", () => {
    expect(detectMentionTrigger("line1\n@alp", 10)).toEqual({
      start: 6,
      query: "alp",
      caret: 10,
    });
  });
});

describe("filterMentionCandidates", () => {
  it("returns the first N candidates when the query is empty", () => {
    const out = filterMentionCandidates(agents, "", 3);
    expect(out.map((a) => a.id)).toEqual(["1", "2", "3"]);
  });

  it("matches case-insensitively as a prefix first", () => {
    const out = filterMentionCandidates(agents, "AL");
    expect(out.map((a) => a.id)).toEqual(["1", "2"]);
  });

  it("falls back to substring matches after prefix matches", () => {
    const out = filterMentionCandidates(agents, "alpha");
    expect(out.map((a) => a.id)).toEqual(["1"]);
  });

  it("returns an empty array when nothing matches", () => {
    expect(filterMentionCandidates(agents, "zzz")).toEqual([]);
  });

  it("matches against agent id as a fallback", () => {
    const out = filterMentionCandidates(agents, "6");
    expect(out.map((a) => a.id)).toEqual(["6"]);
  });

  it("respects the limit argument", () => {
    const out = filterMentionCandidates(agents, "", 2);
    expect(out).toHaveLength(2);
  });
});

describe("replaceMention", () => {
  it("replaces the @-run with @name and a trailing space, moving the caret", () => {
    const ctx = detectMentionTrigger("hi @al", 6)!;
    const out = replaceMention("hi @al", ctx, "alpha");
    expect(out.value).toBe("hi @alpha ");
    expect(out.caret).toBe(10);
  });

  it("is a no-op for an empty name", () => {
    const ctx = detectMentionTrigger("hi @al", 6)!;
    const out = replaceMention("hi @al", ctx, "");
    expect(out.value).toBe("hi @al");
    expect(out.caret).toBe(6);
  });

  it("preserves text before and after the mention run", () => {
    // caret sits between 'o' and the space, so ' b' is outside the run.
    const ctx = detectMentionTrigger("a @bravo b", 8)!;
    const out = replaceMention("a @bravo b", ctx, "Bravo");
    expect(out.value).toBe("a @Bravo  b");
    expect(out.caret).toBe(9);
  });

  it("works with CJK display names", () => {
    const ctx = detectMentionTrigger("看 @评审员", 6)!;
    const out = replaceMention("看 @评审员", ctx, "评审员");
    // Replacement inserts `@name ` (5 chars: @ + 3 CJK + space) at index 2,
    // so the new caret sits just after the trailing space.
    expect(out.value).toBe("看 @评审员 ");
    expect(out.caret).toBe(7);
  });

  it("closes when the user types a punctuation boundary character after the query", () => {
    // `?` is a mention boundary, so the popover should close immediately
    // when it appears after the query.
    expect(detectMentionTrigger("看 @评审员?", 7)).toBeNull();
  });
});

describe("wrapActiveIndex", () => {
  it("loops forward past the last item back to 0", () => {
    expect(wrapActiveIndex(3, 3)).toBe(0);
    expect(wrapActiveIndex(5, 3)).toBe(2);
  });

  it("loops backward from item 0 to the last item", () => {
    expect(wrapActiveIndex(-1, 3)).toBe(2);
    expect(wrapActiveIndex(-4, 3)).toBe(2);
  });

  it("returns 0 when the candidate list is empty", () => {
    expect(wrapActiveIndex(0, 0)).toBe(0);
    expect(wrapActiveIndex(5, 0)).toBe(0);
  });

  it("returns the in-range index unchanged", () => {
    expect(wrapActiveIndex(0, 3)).toBe(0);
    expect(wrapActiveIndex(1, 3)).toBe(1);
    expect(wrapActiveIndex(2, 3)).toBe(2);
  });
});

describe("reduceMentionKey", () => {
  const openState = { popoverOpen: true, candidateCount: 3, activeIndex: 0 };
  const closedState = { popoverOpen: false, candidateCount: 0, activeIndex: 0 };

  it("ArrowDown asks for the next item when the popover is open", () => {
    expect(reduceMentionKey("ArrowDown", openState)).toEqual({
      handled: true,
      action: "next",
    });
  });

  it("ArrowUp asks for the previous item when the popover is open", () => {
    expect(reduceMentionKey("ArrowUp", openState)).toEqual({
      handled: true,
      action: "prev",
    });
  });

  it("Enter commits the active candidate", () => {
    expect(reduceMentionKey("Enter", { ...openState, activeIndex: 2 })).toEqual({
      handled: true,
      action: "commit",
      index: 2,
    });
  });

  it("Tab also commits the active candidate", () => {
    expect(reduceMentionKey("Tab", { ...openState, activeIndex: 1 })).toEqual({
      handled: true,
      action: "commit",
      index: 1,
    });
  });

  it("Enter falls back to index 0 if activeIndex is out of range", () => {
    expect(
      reduceMentionKey("Enter", { ...openState, activeIndex: 99 }),
    ).toEqual({ handled: true, action: "commit", index: 0 });
    expect(
      reduceMentionKey("Enter", { ...openState, activeIndex: -3 }),
    ).toEqual({ handled: true, action: "commit", index: 0 });
  });

  it("Enter does not commit when the popover is closed (lets the textarea insert a newline)", () => {
    expect(reduceMentionKey("Enter", closedState)).toEqual({ handled: false });
  });

  it("ArrowDown is unhandled when the popover is closed", () => {
    expect(reduceMentionKey("ArrowDown", closedState)).toEqual({
      handled: false,
    });
    expect(reduceMentionKey("ArrowUp", closedState)).toEqual({ handled: false });
  });

  it("Enter is unhandled when there are zero candidates", () => {
    expect(
      reduceMentionKey("Enter", { popoverOpen: true, candidateCount: 0, activeIndex: 0 }),
    ).toEqual({ handled: false });
  });

  it("Escape always closes the popover, even when it is already hidden", () => {
    expect(reduceMentionKey("Escape", openState)).toEqual({
      handled: true,
      action: "close",
    });
    expect(reduceMentionKey("Escape", closedState)).toEqual({
      handled: true,
      action: "close",
    });
  });

  it("ignores unrelated keys", () => {
    // Cast to the MentionKey union so we can probe the reducer with
    // unsupported values without TS rejecting the test file.
    const state = { popoverOpen: true, candidateCount: 2, activeIndex: 0 };
    expect(
      reduceMentionKey("a" as unknown as Parameters<typeof reduceMentionKey>[0], state),
    ).toEqual({ handled: false });
  });
});
