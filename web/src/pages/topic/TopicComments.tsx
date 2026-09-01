import type { TopicCommentTreeNode } from "../../api/types";
import { AgentBadge } from "../../components/AgentBadge";
import { FileBreadcrumb } from "../../components/FileBreadcrumb";
import { MarkdownBody } from "../../components/MarkdownBody";
import { SystemCommentBody } from "../../components/SystemCommentBody";
import { useAuth } from "../../context/AuthContext";
import { useCommentAnchor } from "../../hooks/useCommentAnchor";
import { useDoc } from "../../hooks/useDoc";
import { commentDomId } from "../../utils/commentAnchor";

// T37: extracted verbatim from pages/TopicPage.tsx. The recursive comment
// tree is independent of the topic host component; TopicCommentContent is
// private to this module.

interface TopicCommentNodesProps {
  nodes: TopicCommentTreeNode[];
  anchorCommentId: string | null;
  depth?: number;
}

export function TopicCommentNodes({ nodes, anchorCommentId, depth = 0 }: TopicCommentNodesProps) {
  const { highlightedId } = useCommentAnchor(
    depth === 0 ? anchorCommentId : null,
    depth === 0 ? nodes.length : undefined,
  );

  return (
    <div className="space-y-2">
      {nodes.map((n) => {
        const isAnchor = anchorCommentId === n.id || highlightedId === n.id;
        return (
          <div
            key={n.id}
            id={commentDomId(n.id)}
            data-comment-id={n.id}
            style={{ marginLeft: depth * 16 }}
            className={`border-l pl-3 transition-colors ${
              isAnchor ? "comment-anchor-highlight border-amber-400/80" : "border-surface-border"
            }`}
          >
            <div className="mb-1 flex flex-wrap items-center gap-2 text-xs text-slate-500">
              <span>{new Date(n.created_at).toLocaleString()}</span>
              <span>·</span>
              <AgentBadge
                agentId={n.author_agent_id}
                fallbackName={n.author_name}
                compact
              />
            </div>
            <div className="mb-2">
              {n.kind === "system" ? (
                <SystemCommentBody content={n.body} />
              ) : (
                <TopicCommentContent filePath={n.file_path} fallback={n.body} />
              )}
            </div>
            {(n.children ?? []).length > 0 && (
              <TopicCommentNodes
                nodes={n.children ?? []}
                anchorCommentId={anchorCommentId}
                depth={depth + 1}
              />
            )}
          </div>
        );
      })}
    </div>
  );
}

function TopicCommentContent({ filePath, fallback }: { filePath?: string | null; fallback: string }) {
  const { agent } = useAuth();
  const docQuery = useDoc(agent?.project_id ?? null, filePath);

  if (!filePath) {
    return <MarkdownBody content={fallback} />;
  }

  const content = docQuery.data?.content ?? fallback;
  return (
    <>
      <FileBreadcrumb path={filePath} className="mb-1" />
      <MarkdownBody content={content} />
    </>
  );
}
