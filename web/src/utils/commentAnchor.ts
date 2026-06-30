/**
 * Comment anchor utilities.
 *
 * The Web front-end accepts a `comment_id` either as a `?anchor=<id>` query
 * parameter or as a `#comment-<id>` hash fragment. Both forms are normalised
 * into a single comment id string and used by the topic / experiment pages
 * to scroll the matching comment into view and briefly highlight it.
 *
 * Pure helpers live here so they can be exercised by unit tests without a DOM.
 */

const COMMENT_HASH_PREFIX = "comment-";

/** Build a deterministic DOM id for a comment. */
export function commentDomId(commentId: string): string {
  return `${COMMENT_HASH_PREFIX}${commentId}`;
}

/**
 * Pull the target comment id from a URL `search` string and `hash` fragment.
 *
 * Returns `null` when neither form is present, when the value is empty, or when
 * it does not look like a reasonable id (whitespace, control characters, or
 * an over-long string).
 */
export function parseCommentAnchor(search: string, hash: string): string | null {
  const fromQuery = parseAnchorFromSearch(search);
  if (fromQuery) return fromQuery;
  return parseAnchorFromHash(hash);
}

function parseAnchorFromSearch(search: string): string | null {
  if (!search) return null;
  // Strip a leading "?" if present (location.search keeps the leading "?").
  const normalized = search.startsWith("?") ? search.slice(1) : search;
  if (!normalized) return null;
  const params = new URLSearchParams(normalized);
  const raw = params.get("anchor");
  return sanitizeCommentId(raw);
}

function parseAnchorFromHash(hash: string): string | null {
  if (!hash) return null;
  const trimmed = hash.startsWith("#") ? hash.slice(1) : hash;
  if (!trimmed) return null;
  const head = trimmed.split(/[?&\s]/, 1)[0];
  if (!head) return null;
  if (!head.startsWith(COMMENT_HASH_PREFIX)) return null;
  return sanitizeCommentId(head.slice(COMMENT_HASH_PREFIX.length));
}

/**
 * Build an href for a topic / experiment page that auto-anchors to the
 * supplied comment. Pass `commentId=null` to fall back to the plain href.
 */
export function withCommentAnchor(href: string, commentId: string | null | undefined): string {
  if (!commentId) return href;
  const safeId = sanitizeCommentId(commentId);
  if (!safeId) return href;
  // Avoid stacking duplicate anchors if the href already carries one.
  if (parseCommentAnchor(extractSearch(href), extractHash(href)) === safeId) return href;
  const [path, search = "", hash = ""] = splitHref(href);
  const params = new URLSearchParams(search.startsWith("?") ? search.slice(1) : search);
  params.set("anchor", safeId);
  const newSearch = params.toString();
  const newHash = hash || `#${commentDomId(safeId)}`;
  return `${path}${newSearch ? `?${newSearch}` : ""}${newHash}`;
}

function extractSearch(href: string): string {
  const idx = href.indexOf("?");
  if (idx < 0) return "";
  const hashIdx = href.indexOf("#", idx);
  return href.slice(idx, hashIdx >= 0 ? hashIdx : undefined);
}

function extractHash(href: string): string {
  const idx = href.indexOf("#");
  return idx < 0 ? "" : href.slice(idx);
}

function splitHref(href: string): [string, string, string] {
  const hashIdx = href.indexOf("#");
  const pathAndSearch = hashIdx >= 0 ? href.slice(0, hashIdx) : href;
  const hash = hashIdx >= 0 ? href.slice(hashIdx) : "";
  const searchIdx = pathAndSearch.indexOf("?");
  if (searchIdx < 0) return [pathAndSearch, "", hash];
  return [pathAndSearch.slice(0, searchIdx), pathAndSearch.slice(searchIdx), hash];
}

function sanitizeCommentId(value: string | null | undefined): string | null {
  if (!value) return null;
  const trimmed = value.trim();
  if (!trimmed) return null;
  if (trimmed.length > 128) return null;
  // Allow UUIDs / slugs / alphanumerics + dash/underscore. Reject anything
  // containing whitespace, control chars or shell-meta characters so it can be
  // safely interpolated into a DOM id and CSS selector.
  if (!/^[A-Za-z0-9_-]+$/.test(trimmed)) return null;
  return trimmed;
}
