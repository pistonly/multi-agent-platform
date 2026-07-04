/**
 * Pure helpers used by both `TopicPage` and `ExperimentPage` to scroll a
 * comment into view and briefly highlight it.
 *
 * Kept as plain functions so the retry / scroll / highlight / hash-mirror
 * logic can be unit-tested without a DOM environment.
 */

import { commentDomId } from "./commentAnchor";

export interface CommentAnchorTiming {
  /** Total number of times we are willing to retry before giving up. */
  maxAttempts: number;
  /** Time-budget for retries, in milliseconds. */
  totalTimeoutMs: number;
}

export const DEFAULT_ANCHOR_TIMING: CommentAnchorTiming = {
  maxAttempts: 20,
  totalTimeoutMs: 3_000,
};

/**
 * Decide whether we should keep polling for the comment. Returns `false`
 * once we've exhausted either the retry budget or the time budget.
 */
export function shouldKeepRetrying(
  attempts: number,
  elapsedMs: number,
  timing: CommentAnchorTiming = DEFAULT_ANCHOR_TIMING,
): boolean {
  if (attempts >= timing.maxAttempts) return false;
  if (elapsedMs >= timing.totalTimeoutMs) return false;
  return true;
}

/**
 * The outcome of attempting to scroll to a comment. The hook surfaces this
 * for logging + graceful-degradation tests.
 */
export type CommentAnchorAttempt =
  | { kind: "hit"; commentId: string }
  | { kind: "miss"; commentId: string };

/**
 * Look up the comment DOM node by id and, if found, scroll it into view and
 * (optionally) mirror the anchor into `window.location.hash`.
 *
 * The function is intentionally pure: the caller injects `getElementById`
 * and `mirrorHash` so the test suite can substitute deterministic
 * implementations instead of relying on a real DOM.
 */
export function findCommentElement(
  commentId: string,
  getElementById: (id: string) => unknown = defaultGetElementById,
): HTMLElement | null {
  if (!commentId) return null;
  const node = getElementById(commentDomId(commentId));
  return isElement(node) ? (node as HTMLElement) : null;
}

/**
 * One-shot helper: looks up the comment, scrolls it into view, and mirrors
 * the anchor into the URL hash when `mirrorHash` returns `true`. Returns
 * `"hit"` when the element was found and `"miss"` otherwise so the caller
 * can decide whether to retry.
 */
export function tryScrollToComment(
  commentId: string,
  options: {
    getElementById?: (id: string) => unknown;
    mirrorHash?: (id: string) => void;
  } = {},
): CommentAnchorAttempt {
  const target = findCommentElement(commentId, options.getElementById);
  if (!target) {
    return { kind: "miss", commentId };
  }
  try {
    target.scrollIntoView({ behavior: "smooth", block: "center" });
  } catch (err) {
    // Older browsers / jsdom might not implement scrollIntoView smoothly;
    // the test suite relies on the call being *attempted*, so swallow.
    console.warn("[comment-anchor] scrollIntoView failed", err);
  }
  if (options.mirrorHash) {
    try {
      options.mirrorHash(commentId);
    } catch (err) {
      console.warn("[comment-anchor] failed to mirror URL hash", err);
    }
  }
  return { kind: "hit", commentId };
}

function defaultGetElementById(id: string): unknown {
  if (typeof document === "undefined") return null;
  return document.getElementById(id);
}

function isElement(value: unknown): value is Element {
  if (!value || typeof value !== "object") return false;
  // jsdom exposes nodeType === 1 for Element nodes; for the browser this is
  // also true. Other environments (e.g. server) just return null above.
  return (value as { nodeType?: number }).nodeType === 1;
}
