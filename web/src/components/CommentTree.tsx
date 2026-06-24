import { useState } from "react";
import type { CommentTreeNode, ReviewItem } from "../api/types";
import { createComment, updateReviewItem } from "../api/client";

function CommentNode({ node, depth = 0 }: { node: CommentTreeNode; depth?: number }) {
  return (
    <div style={{ marginLeft: depth * 16 }} className="border-l border-surface-border pl-3">
      <div className="mb-1 text-xs text-slate-500">
        {new Date(node.created_at).toLocaleString()} · {node.author_agent_id.slice(0, 8)}…
      </div>
      <p className="mb-2 text-sm text-slate-200">{node.body}</p>
      {node.children.map((child) => (
        <CommentNode key={child.id} node={child} depth={depth + 1} />
      ))}
    </div>
  );
}

interface DisputeSectionProps {
  experimentId: string;
  items: ReviewItem[];
  comments: CommentTreeNode[];
  onUpdated: () => void;
}

export function DisputeSection({ experimentId, items, comments, onUpdated }: DisputeSectionProps) {
  const [replyText, setReplyText] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState<string | null>(null);

  const commentsForItem = (itemId: string) =>
    comments.filter((c) => c.anchor_type === "review_item" && c.anchor_id === itemId);

  async function handleComment(itemId: string) {
    const body = replyText[itemId]?.trim();
    if (!body) return;
    setLoading(itemId);
    try {
      await createComment(experimentId, {
        anchor_type: "review_item",
        anchor_id: itemId,
        body,
      });
      setReplyText((prev) => ({ ...prev, [itemId]: "" }));
      onUpdated();
    } finally {
      setLoading(null);
    }
  }

  async function handleResolve(itemId: string, status: string) {
    setLoading(itemId);
    try {
      await updateReviewItem(itemId, status);
      onUpdated();
    } finally {
      setLoading(null);
    }
  }

  if (items.length === 0) {
    return <p className="text-sm text-slate-500">暂无争议项</p>;
  }

  return (
    <ul className="space-y-4">
      {items.map((item) => (
        <li key={item.id} className="rounded-lg border border-red-900/30 bg-red-950/20 p-4">
          <div className="mb-2 font-medium text-red-200">{item.content}</div>
          <div className="mb-3 space-y-2">
            {commentsForItem(item.id).map((c) => (
              <CommentNode key={c.id} node={c} />
            ))}
          </div>
          <div className="flex flex-wrap gap-2">
            <input
              className="min-w-[200px] flex-1 rounded border border-surface-border bg-surface px-2 py-1 text-sm"
              placeholder="添加评论…"
              value={replyText[item.id] ?? ""}
              onChange={(e) => setReplyText((prev) => ({ ...prev, [item.id]: e.target.value }))}
            />
            <button
              type="button"
              className="btn-secondary"
              disabled={loading === item.id}
              onClick={() => handleComment(item.id)}
            >
              评论
            </button>
            {item.status === "addressed" || item.status === "rebutted" ? (
              <>
                <button
                  type="button"
                  className="btn-primary"
                  disabled={loading === item.id}
                  onClick={() => handleResolve(item.id, "resolved")}
                >
                  认可
                </button>
                <button
                  type="button"
                  className="btn-secondary"
                  disabled={loading === item.id}
                  onClick={() => handleResolve(item.id, "open")}
                >
                  驳回
                </button>
              </>
            ) : null}
          </div>
        </li>
      ))}
    </ul>
  );
}

export function CommentTree({ nodes }: { nodes: CommentTreeNode[] }) {
  const roots = nodes.filter((n) => n.anchor_type !== "review_item");
  if (roots.length === 0) return null;
  return (
    <div className="space-y-3">
      {roots.map((node) => (
        <CommentNode key={node.id} node={node} />
      ))}
    </div>
  );
}
