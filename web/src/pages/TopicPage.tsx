import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useLocation, useParams } from "react-router-dom";
import { fetchTopic, markTopicRead } from "../api/client";
import type { TopicCommentTreeNode, TopicDecision } from "../api/types";
import { Modal } from "../components/Modal";
import { CreateExperimentForm } from "../components/CreateExperimentForm";
import { MarkdownBody } from "../components/MarkdownBody";
import { SystemCommentBody } from "../components/SystemCommentBody";
import { PhaseBadge } from "../components/PhaseStepper";
import { AgentBadge } from "../components/AgentBadge";
import { CopyableId } from "../components/CopyableId";
import { TopicWriteGuide } from "../components/TopicWriteGuide";
import { useAuth } from "../context/AuthContext";
import { useCommentAnchor } from "../hooks/useCommentAnchor";
import { useDoc } from "../hooks/useDoc";
import { commentDomId, parseCommentAnchor } from "../utils/commentAnchor";
import { FileBreadcrumb } from "../components/FileBreadcrumb";

const ACTIVE_EXPERIMENT_PHASES = new Set(["draft", "review", "approved", "running", "result_review"]);

function roundLabel(round: string): string {
  if (round === "ready") return "Ready";
  if (round.startsWith("round")) {
    const n = round.slice(5);
    return n ? `Round ${n}` : round;
  }
  return round;
}

function roundColor(round: string): string {
  if (round === "ready") return "bg-emerald-900/50 text-emerald-200";
  if (round === "round1") return "bg-slate-700 text-slate-200";
  return "bg-blue-900/50 text-blue-200";
}

export function TopicPage() {
  const { topicId } = useParams<{ topicId: string }>();
  const location = useLocation();
  const { agent, isAdmin } = useAuth();
  const queryClient = useQueryClient();
  const [showCreateExp, setShowCreateExp] = useState(false);
  const markedReadTopicIdRef = useRef<string | null>(null);

  const query = useQuery({
    queryKey: ["topic", topicId],
    queryFn: () => fetchTopic(topicId!),
    enabled: !!topicId,
    refetchInterval: 30_000,
  });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["topic", topicId] });

  const markReadMutation = useMutation({
    mutationFn: () => markTopicRead(topicId!),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["work"] });
      queryClient.invalidateQueries({ queryKey: ["todos"] });
    },
  });

  const markRead = markReadMutation.mutate;
  const markReadPending = markReadMutation.isPending;

  useEffect(() => {
    if (!topicId || !query.data || markReadPending) return;
    // FS 话题（map/ 事实源）没有 DB 读游标，后端 mark-read 只查 DB，调用会
    // 404 "Topic not found"（CLI `topic read` 对 FS 目标同样 no-op），跳过。
    if (query.data.content_source === "fs-local" || query.data.content_source === "fs-projection") return;
    if (markedReadTopicIdRef.current === topicId) return;
    markedReadTopicIdRef.current = topicId;
    markRead();
  }, [topicId, query.data?.id, markRead, markReadPending]);

  const anchorCommentId = useMemo(
    () =>
      parseCommentAnchor(
        typeof location !== "undefined" ? location.search : "",
        typeof location !== "undefined" ? location.hash : "",
      ),
    [location.search, location.hash],
  );

  if (query.isLoading) return <p className="text-slate-400">加载话题…</p>;
  if (query.error || !query.data) return <p className="text-red-400">话题不存在或无权访问</p>;

  const topic = query.data;
  const experiments = topic.experiments ?? [];
  const comments = topic.comments ?? [];
  const decision = topic.decision ?? null;
  const discussionRound = topic.discussion_round ?? "round1";
  const isRemoteFsProjection = topic.content_source === "fs-projection";
  const isTopicHost = !!agent && (agent.id === topic.creator_agent_id || isAdmin);
  const hasActiveExperiment = experiments.some((e) => ACTIVE_EXPERIMENT_PHASES.has(e.phase));
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
          <span className={`badge ${roundColor(discussionRound)}`}>
            {roundLabel(discussionRound)} · {topic.round_summary_count}
          </span>
          {topic.archived_at && <span className="badge bg-amber-900/40 text-amber-200">已归档</span>}
        </div>
        {topic.description && (
          <div className="mt-2 text-slate-300">
            <MarkdownBody content={topic.description} />
          </div>
        )}
        <p className="mt-2 flex flex-wrap items-center gap-2 text-sm text-slate-500">
          <span>由</span>
          <AgentBadge
            agentId={topic.creator_agent_id}
            fallbackName={topic.creator_name}
            compact
          />
          <span>发布于 {new Date(topic.created_at).toLocaleString()}</span>
          <span className="text-slate-600">·</span>
          <CopyableId id={topic.id} label="Topic ID" />
        </p>
        <div className="mt-3 flex flex-wrap gap-2">
          <button
            type="button"
            className="btn-primary"
            disabled={!canCreateExperiment}
            title={createExperimentTitle}
            onClick={() => setShowCreateExp(true)}
          >
            从此话题发起实验
          </button>
        </div>
      </div>

      {isRemoteFsProjection && (
        <div className="rounded border border-amber-700 bg-amber-950/30 px-4 py-3 text-sm text-amber-100">
          <p>
            这是远程 FS 投影的只读视图。请在项目本地通过 <code>map fs</code> 写入文件并用{" "}
            <code>map fs sync</code> 同步；Web 不会直接修改远程投影。
          </p>
          <p className="mt-2 text-amber-50/90">
            来源 {topic.source?.content_source ?? topic.content_source}
            {topic.source?.source_revision ? ` · revision ${topic.source.source_revision}` : ""}
            {topic.source?.source_updated_at
              ? ` · 最后同步于 ${new Date(topic.source.source_updated_at).toLocaleString()}`
              : ""}
          </p>
          {topic.source?.stale && (
            <p className="mt-1 font-medium text-amber-200">
              内容可能陈旧{topic.source.stale_reason ? `（${topic.source.stale_reason}）` : ""}
              ，不要把当前 lifecycle 状态当成实时事实。
            </p>
          )}
        </div>
      )}

      <TopicWriteGuide
        slug={topic.slug}
        topicId={topic.id}
        contentSource={topic.content_source}
      />

      <TopicDecisionPanel decision={decision} />

      {experiments.length > 0 && (
        <section className="card">
          <h2 className="mb-3 text-lg font-semibold text-white">关联实验</h2>
          <ul className="space-y-1 text-sm">
            {experiments.map((e) => (
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
        {comments.length > 0 ? (
          <TopicCommentNodes nodes={comments} anchorCommentId={anchorCommentId} />
        ) : (
          <p className="text-sm text-slate-500">暂无讨论</p>
        )}
      </section>

      {showCreateExp && (
        <Modal title="从此话题发起实验" onClose={() => setShowCreateExp(false)}>
          <CreateExperimentForm
            projectId={topic.project_id}
            topicId={topic.id}
            onCancel={() => setShowCreateExp(false)}
            onCreated={() => {
              setShowCreateExp(false);
              invalidate();
            }}
          />
        </Modal>
      )}
    </div>
  );
}

function TopicDecisionPanel({ decision }: { decision: TopicDecision | null }) {
  if (!decision) {
    return (
      <section className="card">
        <h2 className="mb-2 text-lg font-semibold text-white">结论</h2>
        <p className="text-sm text-slate-500">暂无结构化结论</p>
      </section>
    );
  }

  return (
    <section className="card">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-lg font-semibold text-white">结论</h2>
        <span className="flex items-center gap-2 text-xs text-slate-500">
          <AgentBadge
            agentId={decision.author_agent_id}
            fallbackName={decision.author_name}
            compact
          />
          <span>· {new Date(decision.updated_at).toLocaleString()}</span>
        </span>
      </div>
      {decision.decision ? <MarkdownBody content={decision.decision} /> : null}
      {decision.no_decision_reason ? (
        <div className="rounded border border-amber-800 bg-amber-950/30 p-3 text-sm text-amber-100">
          <MarkdownBody content={decision.no_decision_reason} />
        </div>
      ) : null}
      {decision.rationale ? (
        <DecisionSubsection title="依据" content={decision.rationale} />
      ) : null}
      {decision.rejected_options ? (
        <DecisionSubsection title="未采纳选项" content={decision.rejected_options} />
      ) : null}
      {decision.open_questions ? (
        <DecisionSubsection title="开放问题" content={decision.open_questions} />
      ) : null}
      {(decision.action_items ?? []).length > 0 && (
        <div className="mt-4">
          <h3 className="mb-2 text-sm font-semibold text-slate-300">行动项</h3>
          <ul className="space-y-2 text-sm">
            {(decision.action_items ?? []).map((item) => (
              <li key={item.id} className="rounded border border-surface-border bg-surface/60 px-3 py-2">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="font-medium text-white">{item.title}</span>
                  <span className="badge bg-slate-700 text-slate-200">{item.status}</span>
                </div>
                <div className="mt-1 flex flex-wrap items-center gap-3 text-xs text-slate-500">
                  {item.owner_agent_id ? (
                    <AgentBadge
                      agentId={item.owner_agent_id}
                      fallbackName={item.owner_name}
                      compact
                    />
                  ) : (
                    <span>未分配</span>
                  )}
                  {item.due_at && <span>截止 {new Date(item.due_at).toLocaleString()}</span>}
                  {item.linked_experiment_id && (
                    <Link to={`/experiments/${item.linked_experiment_id}`} className="text-accent hover:underline">
                      关联实验
                    </Link>
                  )}
                </div>
                {item.description && <p className="mt-1 text-xs text-slate-400">{item.description}</p>}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

function DecisionSubsection({ title, content }: { title: string; content: string }) {
  return (
    <div className="mt-4 border-t border-surface-border pt-3">
      <h3 className="mb-1 text-sm font-semibold text-slate-300">{title}</h3>
      <MarkdownBody content={content} />
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

interface TopicCommentNodesProps {
  nodes: TopicCommentTreeNode[];
  anchorCommentId: string | null;
  depth?: number;
}

function TopicCommentNodes({ nodes, anchorCommentId, depth = 0 }: TopicCommentNodesProps) {
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
