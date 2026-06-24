import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import {
  approveExperiment,
  completeExperiment,
  fetchCommentTree,
  fetchExperiment,
  fetchLogs,
  fetchPlans,
  fetchReviews,
  startExperiment,
  submitForReview,
} from "../api/client";
import { CommentTree, DisputeSection } from "../components/CommentTree";
import { LogPanel } from "../components/LogPanel";
import { PlanPanel } from "../components/PlanPanel";
import { getUnreasonableItems, ReviewSummary } from "../components/ReviewSummary";
import { PhaseBadge, PhaseStepper } from "../components/PhaseStepper";

export function ExperimentPage() {
  const { experimentId } = useParams<{ experimentId: string }>();
  const queryClient = useQueryClient();
  const [planVersion, setPlanVersion] = useState<number | null>(null);
  const [completeSummary, setCompleteSummary] = useState("");
  const [completeBody, setCompleteBody] = useState("");

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["experiment", experimentId] });
    queryClient.invalidateQueries({ queryKey: ["plans", experimentId] });
    queryClient.invalidateQueries({ queryKey: ["reviews", experimentId] });
    queryClient.invalidateQueries({ queryKey: ["comments", experimentId] });
    queryClient.invalidateQueries({ queryKey: ["logs", experimentId] });
  };

  const experimentQuery = useQuery({
    queryKey: ["experiment", experimentId],
    queryFn: () => fetchExperiment(experimentId!),
    enabled: !!experimentId,
  });

  const plansQuery = useQuery({
    queryKey: ["plans", experimentId],
    queryFn: () => fetchPlans(experimentId!),
    enabled: !!experimentId,
  });

  const reviewsQuery = useQuery({
    queryKey: ["reviews", experimentId],
    queryFn: () => fetchReviews(experimentId!),
    enabled: !!experimentId,
  });

  const commentsQuery = useQuery({
    queryKey: ["comments", experimentId],
    queryFn: () => fetchCommentTree(experimentId!),
    enabled: !!experimentId,
  });

  const logsQuery = useQuery({
    queryKey: ["logs", experimentId],
    queryFn: () => fetchLogs(experimentId!),
    enabled: !!experimentId,
  });

  const actionMutation = useMutation({
    mutationFn: async (action: string) => {
      if (!experimentId) return;
      switch (action) {
        case "submit":
          return submitForReview(experimentId);
        case "approve":
          return approveExperiment(experimentId);
        case "start":
          return startExperiment(experimentId);
        case "complete":
          return completeExperiment(experimentId, {
            summary: completeSummary,
            content_md: completeBody,
          });
      }
    },
    onSuccess: invalidate,
  });

  if (experimentQuery.isLoading) return <p className="text-slate-400">加载实验…</p>;
  if (experimentQuery.error || !experimentQuery.data) {
    return <p className="text-red-400">实验不存在或加载失败</p>;
  }

  const experiment = experimentQuery.data;
  const plans = plansQuery.data ?? [];
  const version = planVersion ?? experiment.current_plan_version;
  const selectedPlan = plans.find((p) => p.version === version) ?? experiment.current_plan;
  const reviews = reviewsQuery.data ?? [];
  const unreasonable = getUnreasonableItems(reviews);

  return (
    <div className="space-y-6">
      <div>
        <Link to={`/projects/${experiment.project_id}`} className="text-sm text-slate-500 hover:text-white">
          ← 项目
        </Link>
        <div className="mt-2 flex flex-wrap items-center gap-3">
          <h1 className="text-2xl font-bold text-white">{experiment.title}</h1>
          <PhaseBadge phase={experiment.phase} />
        </div>
        {experiment.description && <p className="mt-2 text-slate-300">{experiment.description}</p>}
      </div>

      <section className="card">
        <PhaseStepper phase={experiment.phase} />
        <div className="mt-4 flex flex-wrap gap-2">
          {experiment.phase === "draft" && (
            <button type="button" className="btn-primary" onClick={() => actionMutation.mutate("submit")}>
              提交评审
            </button>
          )}
          {experiment.phase === "review" && experiment.open_unreasonable_count === 0 && (
            <button type="button" className="btn-primary" onClick={() => actionMutation.mutate("approve")}>
              批准实验
            </button>
          )}
          {experiment.phase === "approved" && (
            <button type="button" className="btn-primary" onClick={() => actionMutation.mutate("start")}>
              开始执行
            </button>
          )}
          {experiment.phase === "running" && (
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
                onClick={() => actionMutation.mutate("complete")}
              >
                完成实验
              </button>
            </div>
          )}
        </div>
        {actionMutation.isError && (
          <p className="mt-2 text-sm text-red-400">操作失败，请确认当前阶段与权限</p>
        )}
      </section>

      <div className="grid gap-4 lg:grid-cols-2">
        <PlanPanel
          plan={selectedPlan}
          versions={plans}
          version={version}
          onSelectVersion={setPlanVersion}
        />
        <ReviewSummary reviews={reviews} />
      </div>

      <section className="card">
        <h2 className="mb-4 text-lg font-semibold text-white">争议与讨论</h2>
        <DisputeSection
          experimentId={experimentId!}
          items={unreasonable}
          comments={commentsQuery.data ?? []}
          onUpdated={invalidate}
        />
        <div className="mt-6 border-t border-surface-border pt-4">
          <h3 className="mb-2 text-sm font-medium text-slate-400">其他讨论</h3>
          <CommentTree nodes={commentsQuery.data ?? []} />
        </div>
      </section>

      <section className="card">
        <h2 className="mb-4 text-lg font-semibold text-white">实验日志</h2>
        <LogPanel logs={logsQuery.data ?? []} />
      </section>
    </div>
  );
}
