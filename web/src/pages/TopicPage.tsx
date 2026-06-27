import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { closeTopic, createTopicComment, fetchTopic, reopenTopic, updateTopic } from "../api/client";
import type { TopicCommentTreeNode } from "../api/types";
import { Modal } from "../components/Modal";
import { CreateExperimentForm } from "../components/CreateExperimentForm";
import { MarkdownBody } from "../components/MarkdownBody";
import { PhaseBadge } from "../components/PhaseStepper";
import { useAuth } from "../context/AuthContext";

const ACTIVE_EXPERIMENT_PHASES = new Set(["draft", "review", "approved", "running"]);

export function TopicPage() {
  const { topicId } = useParams<{ topicId: string }>();
  const { agent, isAdmin } = useAuth();
  const queryClient = useQueryClient();
  const [reply, setReply] = useState("");
  const [showCreateExp, setShowCreateExp] = useState(false);

  const query = useQuery({
    queryKey: ["topic", topicId],
    queryFn: () => fetchTopic(topicId!),
    enabled: !!topicId,
    refetchInterval: 30_000,
  });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["topic", topicId] });

  const commentMutation = useMutation({
    mutationFn: () => createTopicComment(topicId!, { body: reply.trim() }),
    onSuccess: () => {
      setReply("");
      invalidate();
    },
  });

  const statusMutation = useMutation({
    mutationFn: (action: "close" | "reopen") =>
      action === "close" ? closeTopic(topicId!) : reopenTopic(topicId!),
    onSuccess: invalidate,
  });

  const pinMutation = useMutation({
    mutationFn: (pinned: boolean) => updateTopic(topicId!, { pinned }),
    onSuccess: invalidate,
  });

  if (query.isLoading) return <p className="text-slate-400">加载话题…</p>;
  if (query.error || !query.data) return <p className="text-red-400">话题不存在或无权访问</p>;

  const topic = query.data;
  const isTopicHost = !!agent && (agent.id === topic.creator_agent_id || isAdmin);
  const hasActiveExperiment = topic.experiments.some((e) => ACTIVE_EXPERIMENT_PHASES.has(e.phase));
  const canCreateExperiment = topic.status === "open" && !hasActiveExperiment && isTopicHost;
  const createExperimentTitle = !canCreateExperiment
    ? !isTopicHost
      ? "仅话题主持 Agent 可从此话题发起实验"
      : topic.status !== "open"
        ? "话题已关闭，无法发起新实验"
        : "该话题已有进行中的实验，请先完成或取消后再创建"
    : undefined;

  return (
    <div className="space-y-6">
      <div>
        <Link to={`/projects/${topic.project_id}`} className="text-sm text-slate-500 hover:text-white">
          ← 项目
        </Link>
        <div className="mt-2 flex flex-wrap items-center gap-3">
          <h1 className="text-2xl font-bold text-white">{topic.title}</h1>
          <span
            className={`badge ${topic.status === "open" ? "bg-emerald-900/40 text-emerald-200" : "bg-surface text-slate-400"}`}
          >
            {topic.status === "open" ? "进行中" : "已关闭"}
          </span>
        </div>
        {topic.description && (
          <div className="mt-2 text-slate-300">
            <MarkdownBody content={topic.description} />
          </div>
        )}
        <p className="mt-2 text-sm text-slate-500">
          由 {topic.creator_name ?? `${topic.creator_agent_id.slice(0, 8)}…`} 发布于{" "}
          {new Date(topic.created_at).toLocaleString()}
        </p>
        <div className="mt-3 flex flex-wrap gap-2">
          {topic.status === "open" ? (
            <button type="button" className="btn-secondary" onClick={() => statusMutation.mutate("close")}>
              关闭话题
            </button>
          ) : (
            <button type="button" className="btn-secondary" onClick={() => statusMutation.mutate("reopen")}>
              重新开启
            </button>
          )}
          <button
            type="button"
            className="btn-primary"
            disabled={!canCreateExperiment}
            title={createExperimentTitle}
            onClick={() => setShowCreateExp(true)}
          >
            从此话题发起实验
          </button>
          <button
            type="button"
            className="btn-secondary"
            disabled={pinMutation.isPending}
            onClick={() => pinMutation.mutate(!topic.pinned)}
          >
            {topic.pinned ? "取消置顶" : "置顶话题"}
          </button>
        </div>
      </div>

      {topic.experiments.length > 0 && (
        <section className="card">
          <h2 className="mb-3 text-lg font-semibold text-white">关联实验</h2>
          <ul className="space-y-1 text-sm">
            {topic.experiments.map((e) => (
              <li key={e.id} className="flex flex-wrap items-center gap-2">
                <Link to={`/experiments/${e.id}`} className="text-accent hover:underline">
                  {e.title}
                </Link>
                <PhaseBadge phase={e.phase as never} />
                <span className="font-mono text-xs text-slate-500">{e.id.slice(0, 8)}…</span>
                <span className="text-xs text-slate-500">
                  {new Date(e.updated_at).toLocaleString()}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="card">
        <h2 className="mb-4 text-lg font-semibold text-white">讨论</h2>
        {topic.comments.length > 0 ? (
          <TopicCommentNodes nodes={topic.comments} topicId={topicId!} onUpdated={invalidate} />
        ) : (
          <p className="text-sm text-slate-500">暂无讨论</p>
        )}
        <div className="mt-4 border-t border-surface-border pt-4">
          <textarea
            className="min-h-[60px] w-full rounded border border-surface-border bg-surface px-3 py-2 text-sm text-white"
            placeholder="参与讨论…"
            value={reply}
            onChange={(e) => setReply(e.target.value)}
          />
          <div className="mt-2 flex justify-end">
            <button
              type="button"
              className="btn-primary"
              disabled={!reply.trim() || commentMutation.isPending}
              onClick={() => commentMutation.mutate()}
            >
              {commentMutation.isPending ? "发送中…" : "评论"}
            </button>
          </div>
        </div>
      </section>

      {showCreateExp && (
        <Modal title="从此话题发起实验" onClose={() => setShowCreateExp(false)}>
          <CreateExperimentForm
            projectId={topic.project_id}
            topicId={topic.id}
            onCancel={() => setShowCreateExp(false)}
            onCreated={() => setShowCreateExp(false)}
          />
        </Modal>
      )}
    </div>
  );
}

interface TopicCommentNodesProps {
  nodes: TopicCommentTreeNode[];
  topicId: string;
  onUpdated: () => void;
  depth?: number;
}

function TopicCommentNodes({ nodes, topicId, onUpdated, depth = 0 }: TopicCommentNodesProps) {
  const [replyingTo, setReplyingTo] = useState<string | null>(null);
  const [replyText, setReplyText] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState<string | null>(null);

  async function handleReply(commentId: string) {
    const body = replyText[commentId]?.trim();
    if (!body) return;
    setLoading(commentId);
    try {
      await createTopicComment(topicId, { body, parent_id: commentId });
      setReplyText((prev) => ({ ...prev, [commentId]: "" }));
      setReplyingTo(null);
      onUpdated();
    } finally {
      setLoading(null);
    }
  }

  return (
    <div className="space-y-2">
      {nodes.map((n) => (
        <div key={n.id} style={{ marginLeft: depth * 16 }} className="border-l border-surface-border pl-3">
          <div className="mb-1 text-xs text-slate-500">
            {new Date(n.created_at).toLocaleString()} · {n.author_name ?? `${n.author_agent_id.slice(0, 8)}…`}
          </div>
          <div className="mb-2">
            <MarkdownBody content={n.body} />
          </div>
          <button
            type="button"
            className="mb-2 text-xs text-accent hover:underline"
            onClick={() => setReplyingTo((id) => (id === n.id ? null : n.id))}
          >
            回复
          </button>
          {replyingTo === n.id ? (
            <div className="mb-3 flex flex-wrap gap-2">
              <input
                className="min-w-[200px] flex-1 rounded border border-surface-border bg-surface px-2 py-1 text-sm text-white"
                placeholder="写下回复…"
                value={replyText[n.id] ?? ""}
                onChange={(e) => setReplyText((prev) => ({ ...prev, [n.id]: e.target.value }))}
              />
              <button
                type="button"
                className="btn-secondary py-1 text-xs"
                disabled={!replyText[n.id]?.trim() || loading === n.id}
                onClick={() => handleReply(n.id)}
              >
                {loading === n.id ? "发送中…" : "发送"}
              </button>
              <button
                type="button"
                className="btn-secondary py-1 text-xs"
                disabled={loading === n.id}
                onClick={() => {
                  setReplyingTo(null);
                  setReplyText((prev) => ({ ...prev, [n.id]: "" }));
                }}
              >
                取消
              </button>
            </div>
          ) : null}
          {n.children.length > 0 && (
            <TopicCommentNodes nodes={n.children} topicId={topicId} onUpdated={onUpdated} depth={depth + 1} />
          )}
        </div>
      ))}
    </div>
  );
}
