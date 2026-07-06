/**
 * Markdown-aware @mention extraction — mirrors server `mention_service.extract_mention_names`.
 * Used by Web highlight so display stays aligned with API parsing (skips code spans).
 */

const MENTION_PATTERN = /@([a-zA-Z][a-zA-Z0-9_-]*)/g;

function* iterTextOutsideMarkdownCode(body: string): Generator<string> {
  let i = 0;
  const n = body.length;
  while (i < n) {
    if (body.startsWith("```", i) || body.startsWith("~~~", i)) {
      const fence = body.slice(i, i + 3);
      i += 3;
      while (i < n && body[i] !== "\n") i += 1;
      if (i < n) i += 1;
      while (i < n) {
        if (body.startsWith(fence, i)) {
          i += 3;
          break;
        }
        i += 1;
      }
      continue;
    }
    if (body[i] === "`") {
      let j = i;
      while (j < n && body[j] === "`") j += 1;
      const tickCount = j - i;
      i = j;
      while (i < n) {
        if (body[i] === "`") {
          let k = i;
          while (k < n && body[k] === "`") k += 1;
          if (k - i >= tickCount) {
            i = k;
            break;
          }
        }
        i += 1;
      }
      continue;
    }
    const start = i;
    while (
      i < n &&
      body[i] !== "`" &&
      !body.startsWith("```", i) &&
      !body.startsWith("~~~", i)
    ) {
      i += 1;
    }
    if (start < i) yield body.slice(start, i);
  }
}

/** Agent names mentioned outside Markdown code spans (deduped, first-seen order). */
export function extractMentionNames(body: string): string[] {
  const seen = new Set<string>();
  const names: string[] = [];
  for (const segment of iterTextOutsideMarkdownCode(body)) {
    for (const match of segment.matchAll(MENTION_PATTERN)) {
      const name = match[1];
      if (!seen.has(name)) {
        seen.add(name);
        names.push(name);
      }
    }
  }
  return names;
}

export interface MentionSpan {
  start: number;
  end: number;
  name: string;
}

/** Non-overlapping @mention spans in plain text (caller must only pass code-free segments). */
export function findMentionSpansInPlainText(text: string): MentionSpan[] {
  const spans: MentionSpan[] = [];
  for (const match of text.matchAll(MENTION_PATTERN)) {
    const name = match[1];
    const start = match.index ?? 0;
    spans.push({ start, end: start + match[0].length, name });
  }
  return spans;
}
