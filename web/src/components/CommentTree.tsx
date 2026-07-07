import { useState } from "react";
import type { CommentTreeNode, ReviewItem } from "../api/types";
import { createComment, updateReviewItem } from "../api/client";
import { commentDomId } from "../utils/commentAnchor";
import { MarkdownBody } from "./MarkdownBody";
import { AgentBadge } from "./AgentBadge";
import { AgentMentionInput } from "./AgentMentionInput";

interface CommentNodeProps {
  node: CommentTreeNode;
  depth?: number;
  experimentId: string;
  anchorCommentId?: string | null;
  highlightedId?: string | null;
  onUpdated: () => void;
}

function CommentNode({
  node,
  depth = 0,
  experimentId,
  anchorCommentId = null,
  highlightedId = null,
  onUpdated,
}: CommentNodeProps) {
  const [showReply, setShowReply] = useState(false);
  const [replyText, setReplyText] = useState("");
  const [loading, setLoading] = useState(false);
  const isAnchor = anchorCommentId === node.id || highlightedId === node.id;

  async function handleReply() {
    const body = replyText.trim();
    if (!body) return;
    setLoading(true);
    try {
      await createComment(experimentId, {
        anchor_type: "comment",
        anchor_id: node.id,
        parent_id: node.id,
        body,
      });
      setReplyText("");
      setShowReply(false);
      onUpdated();
    } finally {
      setLoading(false);
    }
  }

  return (
    <div
      id={commentDomId(node.id)}
      data-comment-id={node.id}
      style={{ marginLeft: depth * 16 }}
      className={`border-l pl-3 transition-colors ${
        isAnchor ? "comment-anchor-highlight border-amber-400/80" : "border-surface-border"
      }`}
    >
      <div className="mb-1 flex flex-wrap items-center gap-2 text-xs text-slate-500">
        <span>{new Date(node.created_at).toLocaleString()}</span>
        <span>·</span>
        <AgentBadge
          agentId={node.author_agent_id}
          fallbackName={node.author_name}
          compact
        />
      </div>
      <div className="mb-2">
        <MarkdownBody content={node.body} />
      </div>
      <button
        type="button"
        className="mb-2 text-xs text-accent hover:underline"
        onClick={() => setShowReply((v) => !v)}
      >
        回复
      </button>
      {showReply ? (
        <div className="mb-3 flex flex-wrap gap-2">
          <AgentMentionInput
            className="min-w-[200px] flex-1 rounded border border-surface-border bg-surface px-2 py-1 text-sm text-white"
            placeholder="写下回复… 输入 @ 触发 agent 候选"
            value={replyText}
            onValueChange={setReplyText}
          />
          <button
            type="button"
            className="btn-secondary py-1 text-xs"
            disabled={!replyText.trim() || loading}
            onClick={handleReply}
          >
            {loading ? "发送中…" : "发送"}
          </button>
          <button
            type="button"
            className="btn-secondary py-1 text-xs"
            disabled={loading}
            onClick={() => {
              setShowReply(false);
              setReplyText("");
            }}
          >
            取消
          </button>
        </div>
      ) : null}
      {(node.children ?? []).map((child) => (
        <CommentNode
          key={child.id}
          node={child}
          depth={depth + 1}
          experimentId={experimentId}
          anchorCommentId={anchorCommentId}
          highlightedId={highlightedId}
          onUpdated={onUpdated}
        />
      ))}
    </div>
  );
}

interface DisputeSectionProps {
  experimentId: string;
  items: ReviewItem[];
  comments: CommentTreeNode[];
  anchorCommentId?: string | null;
  highlightedId?: string | null;
  onUpdated: () => void;
}

export function DisputeSection({
  experimentId,
  items,
  comments,
  anchorCommentId = null,
  highlightedId = null,
  onUpdated,
}: DisputeSectionProps) {
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
              <CommentNode
                key={c.id}
                node={c}
                experimentId={experimentId}
                anchorCommentId={anchorCommentId}
                highlightedId={highlightedId}
                onUpdated={onUpdated}
              />
            ))}
          </div>
          <div className="flex flex-wrap gap-2">
            <AgentMentionInput
              className="min-w-[200px] flex-1 rounded border border-surface-border bg-surface px-2 py-1 text-sm"
              placeholder="添加评论… 输入 @ 触发 agent 候选"
              value={replyText[item.id] ?? ""}
              onValueChange={(next) =>
                setReplyText((prev) => ({ ...prev, [item.id]: next }))
              }
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

interface CommentTreeProps {
  nodes: CommentTreeNode[];
  experimentId: string;
  anchorCommentId?: string | null;
  highlightedId?: string | null;
  onUpdated: () => void;
}

export function CommentTree({ nodes, experimentId, anchorCommentId = null, highlightedId = null, onUpdated }: CommentTreeProps) {
  const roots = nodes.filter((n) => n.anchor_type !== "review_item");
  if (roots.length === 0) return null;
  return (
    <div className="space-y-3">
      {roots.map((node) => (
        <CommentNode
          key={node.id}
          node={node}
          experimentId={experimentId}
          anchorCommentId={anchorCommentId}
          highlightedId={highlightedId}
          onUpdated={onUpdated}
        />
      ))}
    </div>
  );
}
