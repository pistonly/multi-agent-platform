import { useEffect, useRef, useState } from "react";
import { commentDomId } from "../utils/commentAnchor";
import {
  DEFAULT_ANCHOR_TIMING,
  shouldKeepRetrying,
  tryScrollToComment,
} from "../utils/commentAnchorScroll";

/** Default duration of the visual highlight applied to the anchored comment. */
export const ANCHOR_HIGHLIGHT_DURATION_MS = 1800;

/**
 * Drives the comment-anchor scroll + highlight behaviour.
 *
 * The hook keeps a `highlightedId` in state so that a freshly-mounted comment
 * can keep its highlight class for the duration of the animation, even when
 * the parent component re-renders for unrelated reasons.
 *
 * It also reflects the resolved anchor into `window.location.hash` (via
 * `history.replaceState`) so that refreshing the page lands at the same
 * comment. All the actual lookup / scroll / retry primitives live in the
 * pure helpers under `utils/commentAnchorScroll.ts` so they can be unit
 * tested without a DOM.
 *
 * Pass `resetKey` to force the effect to re-run when the rendered comment
 * tree changes (e.g. after a network-fetched bundle first renders). Any value
 * that changes when "the comments are now on screen" works well — for example
 * `comments.length`.
 */
export function useCommentAnchor(anchorCommentId: string | null, resetKey?: unknown) {
  const [highlightedId, setHighlightedId] = useState<string | null>(null);
  const highlightTimer = useRef<number | null>(null);

  useEffect(() => {
    if (!anchorCommentId) {
      setHighlightedId(null);
      return;
    }
    let cancelled = false;
    let attempts = 0;
    let elapsed = 0;

    const clearHighlightTimer = () => {
      if (highlightTimer.current !== null) {
        window.clearTimeout(highlightTimer.current);
        highlightTimer.current = null;
      }
    };

    const mirrorHash = (id: string) => {
      if (typeof window === "undefined" || !window.history || !window.history.replaceState) return;
      const newHash = `#${commentDomId(id)}`;
      if (window.location.hash === newHash) return;
      try {
        window.history.replaceState(
          null,
          "",
          `${window.location.pathname}${window.location.search}${newHash}`,
        );
      } catch (err) {
        console.warn("[comment-anchor] failed to mirror URL hash", err);
      }
    };

    const tryScroll = () => {
      if (cancelled) return;
      const result = tryScrollToComment(anchorCommentId, { mirrorHash });
      if (result.kind === "hit") {
        setHighlightedId(anchorCommentId);
        clearHighlightTimer();
        highlightTimer.current = window.setTimeout(() => {
          setHighlightedId((current) => (current === anchorCommentId ? null : current));
          highlightTimer.current = null;
        }, ANCHOR_HIGHLIGHT_DURATION_MS);
        return;
      }
      attempts += 1;
      const stepMs = Math.max(
        1,
        Math.floor(DEFAULT_ANCHOR_TIMING.totalTimeoutMs / Math.max(1, DEFAULT_ANCHOR_TIMING.maxAttempts)),
      );
      elapsed += stepMs;
      if (!shouldKeepRetrying(attempts, elapsed)) {
        // AC-6: graceful degradation when the target can't be found (deleted /
        // paged out / not yet loaded). No white-screen, no infinite retry.
        console.warn(
          `[comment-anchor] target comment ${anchorCommentId} not found after ${attempts} attempts`,
        );
        return;
      }
      window.setTimeout(tryScroll, stepMs);
    };

    // Defer to the next paint so the freshly-fetched comment tree has a chance
    // to mount before we start probing.
    const raf = window.requestAnimationFrame(() => tryScroll());

    return () => {
      cancelled = true;
      window.cancelAnimationFrame(raf);
      clearHighlightTimer();
    };
  }, [anchorCommentId, resetKey]);

  return { highlightedId };
}
