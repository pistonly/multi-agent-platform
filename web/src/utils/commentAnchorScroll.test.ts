import { describe, expect, it, vi } from "vitest";
import {
  DEFAULT_ANCHOR_TIMING,
  findCommentElement,
  shouldKeepRetrying,
  tryScrollToComment,
} from "./commentAnchorScroll";

const UUID = "682125ed-7c34-4291-8dee-c357abccd2bd";

describe("shouldKeepRetrying", () => {
  it("stops once the retry budget is exhausted", () => {
    const timing = { ...DEFAULT_ANCHOR_TIMING, maxAttempts: 5 };
    expect(shouldKeepRetrying(0, 0, timing)).toBe(true);
    expect(shouldKeepRetrying(4, 0, timing)).toBe(true);
    expect(shouldKeepRetrying(5, 0, timing)).toBe(false);
  });

  it("stops once the time budget is exhausted", () => {
    const timing = { maxAttempts: 100, totalTimeoutMs: 500 };
    expect(shouldKeepRetrying(0, 100, timing)).toBe(true);
    expect(shouldKeepRetrying(0, 500, timing)).toBe(false);
    expect(shouldKeepRetrying(0, 999, timing)).toBe(false);
  });
});

describe("findCommentElement", () => {
  it("returns null when the commentId is empty", () => {
    expect(findCommentElement("")).toBeNull();
  });

  it("looks up the comment by the prefixed DOM id", () => {
    const fake = { nodeType: 1 } as unknown as HTMLElement;
    const get = vi.fn().mockReturnValue(fake);
    const result = findCommentElement(UUID, get);
    expect(get).toHaveBeenCalledWith(`comment-${UUID}`);
    expect(result).toBe(fake);
  });

  it("returns null when the lookup yields a non-Element value", () => {
    expect(findCommentElement(UUID, () => null)).toBeNull();
    expect(findCommentElement(UUID, () => ({ nodeType: 3 }))).toBeNull();
    expect(findCommentElement(UUID, () => "not-an-element")).toBeNull();
  });
});

describe("tryScrollToComment", () => {
  it("returns 'miss' when the target is not in the DOM", () => {
    const mirrorHash = vi.fn();
    const result = tryScrollToComment(UUID, {
      getElementById: () => null,
      mirrorHash,
    });
    expect(result).toEqual({ kind: "miss", commentId: UUID });
    expect(mirrorHash).not.toHaveBeenCalled();
  });

  it("returns 'hit', scrolls and mirrors the URL hash when the target exists", () => {
    const scrollIntoView = vi.fn();
    const fakeEl = { nodeType: 1, scrollIntoView } as unknown as HTMLElement;
    const mirrorHash = vi.fn();

    const result = tryScrollToComment(UUID, {
      getElementById: () => fakeEl,
      mirrorHash,
    });

    expect(result).toEqual({ kind: "hit", commentId: UUID });
    expect(scrollIntoView).toHaveBeenCalledWith({
      behavior: "smooth",
      block: "center",
    });
    expect(mirrorHash).toHaveBeenCalledWith(UUID);
  });

  it("swallows errors from scrollIntoView / mirrorHash so the caller can retry", () => {
    const fakeEl = {
      nodeType: 1,
      scrollIntoView: () => {
        throw new Error("scroll failed");
      },
    } as unknown as HTMLElement;

    // silence the expected console.warn noise during the run
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);

    const result = tryScrollToComment(UUID, {
      getElementById: () => fakeEl,
      mirrorHash: () => {
        throw new Error("hash failed");
      },
    });

    expect(result).toEqual({ kind: "hit", commentId: UUID });
    expect(warn).toHaveBeenCalled();
    warn.mockRestore();
  });

  it("gracefully degrades when the target comment is not in the DOM (AC-6)", () => {
    // AC-6: graceful degradation when anchor points at a non-existent comment
    // id. We must not throw, must not infinitely retry, must report a 'miss'.
    const mirrorHash = vi.fn();
    const result = tryScrollToComment(UUID, {
      getElementById: () => null,
      mirrorHash,
    });

    expect(result).toEqual({ kind: "miss", commentId: UUID });
    expect(mirrorHash).not.toHaveBeenCalled();
  });
});
