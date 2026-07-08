import { useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useLocation, useParams } from "react-router-dom";
import {
  acceptExperimentResult,
  approveExperiment,
  completeExperiment,
  createComment,
  fetchExperimentBundle,
  rejectExperimentResult,
  startExperiment,
  submitForReview,
  updateExperiment,
  withdrawExperiment,
} from "../api/client";
import { CommentTree, DisputeSection } from "../components/CommentTree";
import { LogPanel } from "../components/LogPanel";
import { PlanPanel } from "../components/PlanPanel";
import { getUnreasonableItems, ReviewSummary } from "../components/ReviewSummary";
import { AgentMentionInput } from "../components/AgentMentionInput";
import { PhaseBadge, PhaseStepper } from "../components/PhaseStepper";
import { useAuth } from "../context/AuthContext";
import { useCommentAnchor } from "../hooks/useCommentAnchor";
import { parseCommentAnchor } from "../utils/commentAnchor";
import {
  blockedOnMessage,
  hasExperimentAction,
  shouldShowBlockedBanner,
} from "../utils/experimentCapabilities";

export function ExperimentPage() {
  const { experimentId } = useParams<{ experimentId: string }>();
  const location = useLocation();
  const { agent } = useAuth();
  const queryClient = useQueryClient();
  const [planVersion, setPlanVersion] = useState<number | null>(null);
  const [completeSummary, setCompleteSummary] = useState("");
  const [completeBody, setCompleteBody] = useState("");
  const [resultReviewSummary, setResultReviewSummary] = useState("");
  const [resultReviewBody, setResultReviewBody] = useState("");
  const [editingTitle, setEditingTitle] = useState(false);
  const [titleDraft, setTitleDraft] = useState("");
  const [generalComment, setGeneralComment] = useState("");
  const [highlightPlanRevise, setHighlightPlanRevise] = useState(false);
  const [highlightReviewAdd, setHighlightReviewAdd] = useState(false);
  const planPanelRef = useRef<HTMLDivElement>(null);
  const reviewPanelRef = useRef<HTMLDivElement>(null);

  const bundleKey = ["experiment-bundle", experimentId] as const;

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: bundleKey });
  };

  const bundleQuery = useQuery({
    queryKey: bundleKey,
    queryFn: () => fetchExperimentBundle(experimentId!),
    enabled: !!experimentId,
  });

  const phaseMutation = useMutation({
    mutationFn: async (action: string) => {
      if (!experimentId) return;
      switch (action) {
        case "submit_for_review":
          return submitForReview(experimentId);
        case "approve":
          return approveExperiment(experimentId);
        case "start":
          return startExperiment(experimentId);
        case "withdraw":
          return withdrawExperiment(experimentId);
        case "complete":
          return completeExperiment(experimentId, {
            summary: completeSummary,
            content_md: completeBody,
          });
        case "accept_result":
          return acceptExperimentResult(experimentId, {
            summary: resultReviewSummary,
            content_md: resultReviewBody,
          });
        case "reject_result":
          return rejectExperimentResult(experimentId, {
            summary: resultReviewSummary,
            content_md: resultReviewBody,
          });
      }
    },
    onSuccess: invalidate,
  });

  const titleMutation = useMutation({
    mutationFn: () => updateExperiment(experimentId!, { title: titleDraft.trim() }),
    onSuccess: () => {
      setEditingTitle(false);
      invalidate();
    },
  });

  const archiveMutation = useMutation({
    mutationFn: (archived: boolean) => updateExperiment(experimentId!, { archived }),
    onSuccess: () => {
      invalidate();
      queryClient.invalidateQueries({ queryKey: ["project-experiments"] });
    },
  });

  const commentMutation = useMutation({
    mutationFn: () => {
      const bundle = bundleQuery.data;
      if (!bundle) throw new Error("实验数据未加载");
      const v = planVersion ?? bundle.experiment.current_plan_version ?? 0;
      const plan =
        (bundle.plans ?? []).find((p) => p.version === v) ?? bundle.experiment.current_plan ?? null;
      if (!plan) throw new Error("No active plan to anchor the comment");
      return createComment(experimentId!, {
        anchor_type: "plan",
        anchor_id: plan.id,
        body: generalComment.trim(),
      });
    },
    onSuccess: () => {
      setGeneralComment("");
      invalidate();
    },
  });

  const anchorCommentId = useMemo(
    () => parseCommentAnchor(location.search, location.hash),
    [location.search, location.hash],
  );
  // Hooks must run unconditionally on every render, so we always invoke the
  // anchor hook and only consume `highlightedId` once the bundle is ready.
  // The bundle fetch is asynchronous, so we re-trigger via `comments.length`
  // once the comment tree first mounts.
  const { highlightedId } = useCommentAnchor(
    anchorCommentId,
    bundleQuery.data?.comments?.length,
  );

  if (bundleQuery.isLoading) return <p className="text-slate-400">加载实验…</p>;
  if (bundleQuery.error || !bundleQuery.data) {
    return <p className="text-red-400">实验不存在或加载失败</p>;
  }

  const bundle = bundleQuery.data;
  const experiment = bundle.experiment;
  const plans = bundle.plans ?? [];
  const reviews = bundle.reviews ?? [];
  const comments = bundle.comments ?? [];
  const logs = bundle.logs ?? [];
  const actions = experiment.actions ?? [];
  const blockedMessage = shouldShowBlockedBanner(actions, experiment.blocked_on)
    ? blockedOnMessage(experiment.blocked_on)
    : null;
  const version = planVersion ?? experiment.current_plan_version;
  const selectedPlan = plans.find((p) => p.version === version) ?? experiment.current_plan ?? null;
  const unreasonable = getUnreasonableItems(reviews);
  const canAppendLog =
    experiment.phase === "running" || experiment.phase === "result_review" || experiment.phase === "done";
  const isTerminal = experiment.phase === "done" || experiment.phase === "cancelled";
  const canToggleArchive = isTerminal || !!experiment.archived_at;
  const isCreator = !!agent && agent.id === experiment.creator_agent_id;

  const scrollToPlanRevise = () => {
    setHighlightPlanRevise(true);
    planPanelRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
  };

  const scrollToReviewAdd = () => {
    setHighlightReviewAdd(true);
    reviewPanelRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
  };

  return (
    <div className="space-y-6">
      <div>
        <Link to={`/projects/${experiment.project_id}`} className="text-sm text-slate-500 hover:text-white">
          ← 项目
        </Link>
        <div className="mt-2 flex flex-wrap items-center gap-3">
          {editingTitle ? (
            <>
              <input
                className="rounded border border-surface-border bg-surface px-2 py-1 text-xl font-bold text-white"
                value={titleDraft}
                onChange={(e) => setTitleDraft(e.target.value)}
              />
              <button
                type="button"
                className="btn-primary"
                disabled={!titleDraft.trim() || titleMutation.isPending}
                onClick={() => titleMutation.mutate()}
              >
                保存
              </button>
              <button type="button" className="btn-secondary" onClick={() => setEditingTitle(false)}>
                取消
              </button>
            </>
          ) : (
            <>
              <h1 className="text-2xl font-bold text-white">{experiment.title}</h1>
              {!isTerminal && (
                <button
                  type="button"
                  className="btn-secondary py-1 text-xs"
                  onClick={() => {
                    setTitleDraft(experiment.title);
                    setEditingTitle(true);
                  }}
                >
                  编辑
                </button>
              )}
            </>
          )}
          <PhaseBadge phase={experiment.phase} />
          {experiment.archived_at && <span className="badge bg-amber-900/40 text-amber-200">已归档</span>}
        </div>
        {experiment.description && <p className="mt-2 text-slate-300">{experiment.description}</p>}
        {experiment.topic_id && (
          <p className="mt-1 text-xs text-slate-500">
            来源话题：
            <Link to={`/topics/${experiment.topic_id}`} className="text-accent hover:underline">
              查看
            </Link>
          </p>
        )}
      </div>

      <section className="card">
        <PhaseStepper phase={experiment.phase} />
        {isCreator && canToggleArchive && (
          <div className="mt-3">
            <button
              type="button"
              className="btn-secondary"
              disabled={archiveMutation.isPending}
              onClick={() => archiveMutation.mutate(!experiment.archived_at)}
            >
              {experiment.archived_at ? "取消归档" : "归档实验"}
            </button>
          </div>
        )}
        {blockedMessage && (
          <p className="mt-3 rounded border border-amber-900/50 bg-amber-950/30 px-3 py-2 text-sm text-amber-200">
            {blockedMessage}
          </p>
        )}
        <div className="mt-4 flex flex-wrap gap-2">
          {hasExperimentAction(actions, "submit_for_review") && (
            <button
              type="button"
              className="btn-primary"
              onClick={() => phaseMutation.mutate("submit_for_review")}
            >
              提交评审
            </button>
          )}
          {hasExperimentAction(actions, "approve") && (
            <button type="button" className="btn-primary" onClick={() => phaseMutation.mutate("approve")}>
              确认进入执行
            </button>
          )}
          {hasExperimentAction(actions, "withdraw") && (
            <button type="button" className="btn-secondary" onClick={() => phaseMutation.mutate("withdraw")}>
              撤回修改
            </button>
          )}
          {hasExperimentAction(actions, "start") && (
            <button type="button" className="btn-primary" onClick={() => phaseMutation.mutate("start")}>
              开始执行
            </button>
          )}
          {hasExperimentAction(actions, "plan_revise") && (
            <button type="button" className="btn-secondary" onClick={scrollToPlanRevise}>
              修订计划
            </button>
          )}
          {hasExperimentAction(actions, "review_add") && (
            <button type="button" className="btn-secondary" onClick={scrollToReviewAdd}>
              提交评审意见
            </button>
          )}
          {hasExperimentAction(actions, "complete") && (
            <div className="flex w-full flex-col gap-2 sm:flex-row sm:items-end">
              <input
                className="rounded border border-surface-border bg-surface px-2 py-1 text-sm"
                placeholder="日志摘要"
                value={completeSummary}
                onChange={(e) => setCompleteSummary(e.target.value)}
              />
              <textarea
                className="min-h-[60px] flex-1 rounded border border-surface-border bg-surface px-2 py-1 text-sm"
                placeholder="最终日志 (Markdown)"
                value={completeBody}
                onChange={(e) => setCompleteBody(e.target.value)}
              />
              <button
                type="button"
                className="btn-primary"
                disabled={!completeSummary || !completeBody}
                onClick={() => phaseMutation.mutate("complete")}
              >
                提交结果审批
              </button>
            </div>
          )}
          {(hasExperimentAction(actions, "accept_result") ||
            hasExperimentAction(actions, "reject_result")) && (
            <div className="flex w-full flex-col gap-2 sm:flex-row sm:items-end">
              <input
                className="rounded border border-surface-border bg-surface px-2 py-1 text-sm"
                placeholder="审批摘要"
                value={resultReviewSummary}
                onChange={(e) => setResultReviewSummary(e.target.value)}
              />
              <textarea
                className="min-h-[60px] flex-1 rounded border border-surface-border bg-surface px-2 py-1 text-sm"
                placeholder="审批意见 (Markdown)"
                value={resultReviewBody}
                onChange={(e) => setResultReviewBody(e.target.value)}
              />
              {hasExperimentAction(actions, "accept_result") && (
                <button
                  type="button"
                  className="btn-primary"
                  disabled={!resultReviewSummary || !resultReviewBody}
                  onClick={() => phaseMutation.mutate("accept_result")}
                >
                  通过结果
                </button>
              )}
              {hasExperimentAction(actions, "reject_result") && (
                <button
                  type="button"
                  className="btn-secondary"
                  disabled={!resultReviewSummary || !resultReviewBody}
                  onClick={() => phaseMutation.mutate("reject_result")}
                >
                  驳回返工
                </button>
              )}
            </div>
          )}
        </div>
        {phaseMutation.isError && (
          <p className="mt-2 text-sm text-red-400">操作失败，请确认当前阶段与权限</p>
        )}
      </section>

      <AcceptanceStatusPanel statuses={experiment.acceptance_status ?? []} />

      <div className="grid gap-4 lg:grid-cols-2">
        <div ref={planPanelRef}>
          <PlanPanel
            experimentId={experimentId!}
            plan={selectedPlan}
            versions={plans}
            version={version}
            onSelectVersion={setPlanVersion}
            onUpdated={invalidate}
            canRevise={hasExperimentAction(actions, "plan_revise")}
            highlightRevise={highlightPlanRevise}
          />
        </div>
        <div ref={reviewPanelRef}>
          <ReviewSummary
            experimentId={experimentId!}
            reviews={reviews}
            onUpdated={invalidate}
            canAddReview={hasExperimentAction(actions, "review_add")}
            highlightReview={highlightReviewAdd}
          />
        </div>
      </div>

      <section className="card">
        <h2 className="mb-4 text-lg font-semibold text-white">争议与讨论</h2>
        <DisputeSection
          experimentId={experimentId!}
          items={unreasonable}
          comments={comments}
          anchorCommentId={anchorCommentId}
          highlightedId={highlightedId}
          onUpdated={invalidate}
        />
        <div className="mt-6 border-t border-surface-border pt-4">
          <h3 className="mb-2 text-sm font-medium text-slate-400">其他讨论</h3>
          <CommentTree
            nodes={comments}
            experimentId={experimentId!}
            anchorCommentId={anchorCommentId}
            highlightedId={highlightedId}
            onUpdated={invalidate}
          />
          {selectedPlan ? (
            <div className="mt-3 flex flex-col gap-2 sm:flex-row">
              <AgentMentionInput
                className="flex-1 rounded border border-surface-border bg-surface px-2 py-1 text-sm text-white"
                placeholder="添加讨论… 输入 @ 触发 agent 候选"
                value={generalComment}
                onValueChange={setGeneralComment}
              />
              <button
                type="button"
                className="btn-primary"
                disabled={!generalComment.trim() || commentMutation.isPending}
                onClick={() => commentMutation.mutate()}
              >
                {commentMutation.isPending ? "发送中…" : "评论"}
              </button>
            </div>
          ) : (
            <p className="mt-3 text-xs text-slate-500">暂无计划版本，无法发起讨论</p>
          )}
        </div>
      </section>

      <section className="card">
        <h2 className="mb-4 text-lg font-semibold text-white">实验日志</h2>
        <LogPanel
          experimentId={experimentId!}
          logs={logs}
          canAppend={canAppendLog}
          onUpdated={invalidate}
        />
      </section>
    </div>
  );
}

function AcceptanceStatusPanel({
  statuses,
}: {
  statuses: {
    id: string;
    description: string;
    acceptance_type: string;
    evidence_provided?: boolean;
    reviewer_verdict?: string | null;
  }[];
}) {
  if (statuses.length === 0) return null;
  return (
    <section className="card">
      <h2 className="mb-3 text-lg font-semibold text-white">验收状态</h2>
      <div className="overflow-x-auto">
        <table className="min-w-full text-left text-sm">
          <thead className="text-xs uppercase text-slate-500">
            <tr>
              <th className="pb-2 pr-4">类型</th>
              <th className="pb-2 pr-4">验收项</th>
              <th className="pb-2 pr-4">证据</th>
              <th className="pb-2">评审结论</th>
            </tr>
          </thead>
          <tbody>
            {statuses.map((item) => (
              <tr key={item.id} className="border-t border-surface-border">
                <td className="py-2 pr-4 font-mono text-xs text-slate-300">{item.acceptance_type}</td>
                <td className="py-2 pr-4 text-slate-200">{item.description}</td>
                <td className="py-2 pr-4">
                  <span
                    className={`badge ${
                      item.evidence_provided
                        ? "bg-emerald-900/40 text-emerald-200"
                        : "bg-slate-800 text-slate-300"
                    }`}
                  >
                    {item.evidence_provided ? "已提供" : "未提供"}
                  </span>
                </td>
                <td className="py-2 text-slate-300">{item.reviewer_verdict ?? "待评审"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
