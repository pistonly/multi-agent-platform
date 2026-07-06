import { useState } from "react";
import { MarkdownBody } from "./MarkdownBody";

interface SystemCommentBodyProps {
  content: string;
}

/** Collapsed gray system comment (e.g. round ack); expandable on click. */
export function SystemCommentBody({ content }: SystemCommentBodyProps) {
  const [expanded, setExpanded] = useState(false);
  const preview = content.replace(/\s+/g, " ").trim().slice(0, 80);

  return (
    <div className="rounded border border-surface-border bg-surface/60 text-slate-400">
      <button
        type="button"
        className="w-full px-2 py-1 text-left text-xs hover:text-slate-300"
        onClick={() => setExpanded((v) => !v)}
      >
        {expanded ? "收起系统消息" : `系统消息 · ${preview}${content.length > 80 ? "…" : ""}`}
      </button>
      {expanded ? (
        <div className="border-t border-surface-border px-2 py-2 text-sm opacity-80">
          <MarkdownBody content={content} />
        </div>
      ) : null}
    </div>
  );
}
