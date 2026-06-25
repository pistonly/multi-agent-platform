import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { closeTopic, createTopicComment, fetchTopic, reopenTopic } from "../api/client";
import type { TopicCommentTreeNode } from "../api/types";
import { Modal } from "../components/Modal";
import { CreateExperimentForm } from "../components/CreateExperimentForm";

export function TopicPage() {
  const { topicId } = useParams<{ topicId: string }>();
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

  if (query.isLoading) return <p className="text-slate-400">加载话题…</p>;
  if (query.error || !query.data) return <p className="text-red-400">话题不存在或无权访问</p>;

  const topic = query.data;

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
        {topic.description && <p className="mt-2 whitespace-pre-wrap text-slate-300">{topic.description}</p>}
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
          <button type="button" className="btn-primary" onClick={() => setShowCreateExp(true)}>
            从此话题发起实验
          </button>
        </div>
      </div>

      {topic.experiments.length > 0 && (
        <section className="card">
          <h2 className="mb-3 text-lg font-semibold text-white">关联实验</h2>
          <ul className="space-y-1 text-sm">
            {topic.experiments.map((e) => (
              <li key={e.id}>
                <Link to={`/experiments/${e.id}`} className="text-accent hover:underline">
                  {e.title}
                </Link>
                <span className="ml-2 text-xs text-slate-500">（{e.phase}）</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="card">
        <h2 className="mb-4 text-lg font-semibold text-white">讨论</h2>
        {topic.comments.length > 0 ? (
          <TopicCommentNodes nodes={topic.comments} />
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

function TopicCommentNodes({ nodes, depth = 0 }: { nodes: TopicCommentTreeNode[]; depth?: number }) {
  return (
    <div className="space-y-2">
      {nodes.map((n) => (
        <div key={n.id} style={{ marginLeft: depth * 16 }} className="border-l border-surface-border pl-3">
          <div className="mb-1 text-xs text-slate-500">
            {new Date(n.created_at).toLocaleString()} · {n.author_agent_id.slice(0, 8)}…
          </div>
          <p className="mb-2 text-sm text-slate-200">{n.body}</p>
          {n.children.length > 0 && <TopicCommentNodes nodes={n.children} depth={depth + 1} />}
        </div>
      ))}
    </div>
  );
}
