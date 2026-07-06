import type { ReactNode } from "react";
import { findMentionSpansInPlainText } from "../utils/mentionParse";

/** Highlight @agent tokens in a plain-text segment (not inside Markdown code). */
export function MentionHighlight({ text }: { text: string }): ReactNode {
  const spans = findMentionSpansInPlainText(text);
  if (spans.length === 0) return text;

  const nodes: ReactNode[] = [];
  let cursor = 0;
  for (const span of spans) {
    if (span.start > cursor) {
      nodes.push(text.slice(cursor, span.start));
    }
    nodes.push(
      <span key={span.start} className="font-medium text-accent">
        @{span.name}
      </span>,
    );
    cursor = span.end;
  }
  if (cursor < text.length) {
    nodes.push(text.slice(cursor));
  }
  return <>{nodes}</>;
}
