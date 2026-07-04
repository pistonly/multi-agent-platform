/**
 * Helpers for the @-mention autocomplete in comment inputs.
 *
 * Pure functions only — no DOM access — so the same logic can be exercised
 * by unit tests and shared between the <textarea> (topic / general comment)
 * and <input> (single-line reply) variants of the popover.
 *
 * The popover triggers as soon as the user types `@` while the caret is
 * somewhere inside a "free" run of text (no whitespace before, no newlines).
 * Once the trigger is active we keep re-filtering the candidate list against
 * the substring between the `@` and the caret.
 */

export interface MentionContext {
  /** Index of the `@` that opened the mention (inclusive). */
  start: number;
  /** Text the user has typed after the `@`. Empty string while just `@`. */
  query: string;
  /** Caret position (== start + 1 + query.length). */
  caret: number;
}

/**
 * Detect whether the caret currently sits inside an @-mention run.
 *
 * Returns `null` when:
 *   - the caret is at the very start of the input,
 *   - the character immediately before the caret is not `@` and the
 *     character before the trigger `@` is not whitespace / start-of-string
 *     (so the `@` would be inside an email address or a different token),
 *   - the run contains a whitespace or punctuation boundary character (the
 *     user has "left" the mention and started a new token — close the
 *     popover).
 *
 * The query keeps letters, digits, dot, dash and underscore; anything else
 * closes the popover. Non-ASCII letters (CJK) are part of the run so users
 * can mention agents with Chinese display names.
 */
export function detectMentionTrigger(value: string, caret: number): MentionContext | null {
  if (typeof value !== "string" || caret < 1 || caret > value.length) return null;

  // Walk left from the caret, collecting characters that belong to the
  // mention token, until we hit either a boundary character or the opening
  // `@`.
  let i = caret - 1;
  while (i >= 0) {
    const ch = value[i];
    if (ch === "@") break;
    if (isMentionBoundary(ch)) return null;
    i--;
  }

  if (i < 0 || value[i] !== "@") return null;
  // The character before the `@` (if any) must be whitespace / start so we
  // don't fire inside an email address or a quoted string.
  if (i > 0 && !isMentionBoundary(value[i - 1])) return null;

  const start = i;
  const query = value.slice(start + 1, caret);
  return { start, query, caret };
}

function isMentionBoundary(ch: string | undefined): boolean {
  if (!ch) return true;
  // Whitespace, control chars and any punctuation other than . _ - closes
  // the mention run. Non-ASCII letters (CJK etc.) are part of the run so the
  // user can mention agents with Chinese display names.
  if (/\s/.test(ch)) return true;
  if (/[\x00-\x1F\x7F]/.test(ch)) return true;
  if (/[!"#$%&'()*+,/:;<=>?@[\\\]^`{|}~]/.test(ch)) return true;
  return false;
}

export interface MentionCandidate {
  id: string;
  name: string;
  /** Optional secondary line, e.g. role / project key, displayed dim. */
  hint?: string | null;
}

const MAX_CANDIDATES = 50;

/**
 * Filter + rank a candidate list for the current mention query.
 *
 *  - empty query → return the first `limit` candidates in input order
 *  - non-empty query → case-insensitive substring match, preferring
 *    name-prefix matches first so the most relevant agent rises to the top
 *
 * The returned list is capped at `limit` (default 50) so the popover stays
 * bounded even when the platform has thousands of agents.
 */
export function filterMentionCandidates<T extends MentionCandidate>(
  agents: readonly T[],
  query: string,
  limit: number = MAX_CANDIDATES,
): T[] {
  const q = query.trim().toLowerCase();
  if (!q) return agents.slice(0, limit);

  const prefixMatches: T[] = [];
  const substringMatches: T[] = [];
  for (const agent of agents) {
    const name = agent.name.toLowerCase();
    const id = agent.id.toLowerCase();
    if (name.startsWith(q) || id.startsWith(q)) {
      prefixMatches.push(agent);
    } else if (name.includes(q) || id.includes(q)) {
      substringMatches.push(agent);
    }
  }
  return prefixMatches.concat(substringMatches).slice(0, limit);
}

export interface MentionReplacement {
  /** New full text value for the input. */
  value: string;
  /** Caret position after the inserted mention. */
  caret: number;
}

/**
 * Replace the current mention run with `name` and append a trailing space so
 * the user can keep typing the next token without first escaping the mention.
 *
 * If the trigger context is invalid the function is a no-op and returns the
 * original value.
 */
export function replaceMention(
  value: string,
  ctx: MentionContext,
  name: string,
): MentionReplacement {
  if (!name) return { value, caret: ctx.caret };
  const before = value.slice(0, ctx.start);
  const after = value.slice(ctx.caret);
  const insert = `@${name} `;
  return {
    value: before + insert + after,
    caret: before.length + insert.length,
  };
}

/**
 * Keys handled by the @-mention popover. Centralised so the keyboard
 * reducer can be exercised from a Node test without standing up jsdom.
 */
export type MentionKey =
  | "ArrowDown"
  | "ArrowUp"
  | "Enter"
  | "Tab"
  | "Escape";

export interface MentionKeyState {
  /** Whether the popover is currently open and has candidates to navigate. */
  popoverOpen: boolean;
  /** Number of candidates currently shown (length of the filtered list). */
  candidateCount: number;
  /** Index of the highlighted option (0-based). */
  activeIndex: number;
}

export type MentionKeyResult =
  | { handled: false }
  | { handled: true; action: "next" }
  | { handled: true; action: "prev" }
  | { handled: true; action: "commit"; index: number }
  | { handled: true; action: "close" };

/**
 * Pure reducer that decides how the @-mention popover reacts to a key
 * press. Extracted so unit tests can exercise every branch without rendering
 * the React component. The component is responsible for translating each
 * `action` into a state mutation (active index bump, candidate commit, etc).
 *
 * Branches mirror the acceptance criteria in the experiment plan:
 *  - ↑/↓: navigate, wrap around at the ends (common IDE / IM behaviour)
 *  - Enter / Tab: commit the currently highlighted candidate
 *  - Escape: close the popover
 *  - any other key: not handled, the host should fall through to the
 *    textarea's own onKeyDown so Enter still inserts a newline
 *
 * When the popover isn't open (no trigger context, no candidates) we still
 * consume Escape so a stale popover can always be dismissed; every other
 * key falls through.
 */
export function reduceMentionKey(
  key: MentionKey,
  state: MentionKeyState,
): MentionKeyResult {
  const { popoverOpen, candidateCount, activeIndex } = state;

  if (key === "Escape") {
    // Always consume Escape — closing a hidden popover is a no-op but we
    // still want the browser default (e.g. blur) to be suppressed.
    return { handled: true, action: "close" };
  }

  if (!popoverOpen || candidateCount === 0) {
    return { handled: false };
  }

  if (key === "ArrowDown") {
    return { handled: true, action: "next" };
  }
  if (key === "ArrowUp") {
    return { handled: true, action: "prev" };
  }
  if (key === "Enter" || key === "Tab") {
    const safeIndex =
      activeIndex >= 0 && activeIndex < candidateCount ? activeIndex : 0;
    return { handled: true, action: "commit", index: safeIndex };
  }
  return { handled: false };
}

/**
 * Wraps an index so that ArrowDown at the last item loops back to 0 and
 * ArrowUp at item 0 loops to the last item. Pure helper kept here so the
 * reducer and the component agree on the same wrap-around behaviour.
 */
export function wrapActiveIndex(
  nextIndex: number,
  candidateCount: number,
): number {
  if (candidateCount <= 0) return 0;
  const len = candidateCount;
  return ((nextIndex % len) + len) % len;
}
