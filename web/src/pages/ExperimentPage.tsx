import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import {
  approveExperiment,
  cancelExperiment,
  completeExperiment,
  createComment,
  fetchExperimentBundle,
  startExperiment,
  submitForReview,
  updateExperiment,
  withdrawExperiment,
} from "../api/client";
import { CommentTree, DisputeSection } from "../components/CommentTree";
import { LogPanel } from "../components/LogPanel";
import { PlanPanel } from "../components/PlanPanel";
import { getUnreasonableItems, ReviewSummary } from "../components/ReviewSummary";
import { PhaseBadge, PhaseStepper } from "../components/PhaseStepper";
import { useAuth } from "../context/AuthContext";

export function ExperimentPage() {
  const { experimentId } = useParams<{ experimentId: string }>();
  const { agent } = useAuth();
  const queryClient = useQueryClient();
  const [planVersion, setPlanVersion] = useState<number | null>(null);
  const [completeSummary, setCompleteSummary] = useState("");
  const [completeBody, setCompleteBody] = useState("");
  const [editingTitle, setEditingTitle] = useState(false);
  const [titleDraft, setTitleDraft] = useState("");
  const [generalComment, setGeneralComment] = useState("");

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
        case "submit":
          return submitForReview(experimentId);
        case "approve":
          return approveExperiment(experimentId);
        case "start":
          return startExperiment(experimentId);
        case "withdraw":
          return withdrawExperiment(experimentId);
        case "cancel":
          return cancelExperiment(experimentId);
        case "complete":
          return completeExperiment(experimentId, {
            summary: completeSummary,
            content_md: completeBody,
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
        bundle.plans.find((p) => p.version === v) ?? bundle.experiment.current_plan ?? null;
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

  if (bundleQuery.isLoading) return <p className="text-slate-400">加载实验…</p>;
  if (bundleQuery.error || !bundleQuery.data) {
    return <p className="text-red-400">实验不存在或加载失败</p>;
  }

  const { experiment, plans, reviews, comments, logs } = bundleQuery.data;
  const version = planVersion ?? experiment.current_plan_version;
  const selectedPlan = plans.find((p) => p.version === version) ?? experiment.current_plan;
  const unreasonable = getUnreasonableItems(reviews);
  const canAppendLog = experiment.phase === "running" || experiment.phase === "done";
  const isTerminal = experiment.phase === "done" || experiment.phase === "cancelled";
  const isCreator = !!agent && agent.id === experiment.creator_agent_id;

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
        {isCreator && (
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
        <div className="mt-4 flex flex-wrap gap-2">
          {experiment.phase === "draft" && (
            <button type="button" className="btn-primary" onClick={() => phaseMutation.mutate("submit")}>
              提交评审
            </button>
          )}
          {experiment.phase === "review" && (
            <>
              {experiment.open_unreasonable_count === 0 && (
                <button type="button" className="btn-primary" onClick={() => phaseMutation.mutate("approve")}>
                  批准实验
                </button>
              )}
              <button type="button" className="btn-secondary" onClick={() => phaseMutation.mutate("withdraw")}>
                撤回修改
              </button>
            </>
          )}
          {experiment.phase === "approved" && (
            <button type="button" className="btn-primary" onClick={() => phaseMutation.mutate("start")}>
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
                onClick={() => phaseMutation.mutate("complete")}
              >
                完成实验
              </button>
            </div>
          )}
          {!isTerminal && (
            <button type="button" className="btn-secondary" onClick={() => phaseMutation.mutate("cancel")}>
              取消实验
            </button>
          )}
        </div>
        {phaseMutation.isError && (
          <p className="mt-2 text-sm text-red-400">操作失败，请确认当前阶段与权限</p>
        )}
      </section>

      <div className="grid gap-4 lg:grid-cols-2">
        <PlanPanel
          experimentId={experimentId!}
          plan={selectedPlan}
          versions={plans}
          version={version}
          onSelectVersion={setPlanVersion}
          onUpdated={invalidate}
        />
        <ReviewSummary experimentId={experimentId!} reviews={reviews} onUpdated={invalidate} />
      </div>

      <section className="card">
        <h2 className="mb-4 text-lg font-semibold text-white">争议与讨论</h2>
        <DisputeSection
          experimentId={experimentId!}
          items={unreasonable}
          comments={comments}
          onUpdated={invalidate}
        />
        <div className="mt-6 border-t border-surface-border pt-4">
          <h3 className="mb-2 text-sm font-medium text-slate-400">其他讨论</h3>
          <CommentTree nodes={comments} experimentId={experimentId!} onUpdated={invalidate} />
          {selectedPlan ? (
            <div className="mt-3 flex flex-col gap-2 sm:flex-row">
              <input
                className="flex-1 rounded border border-surface-border bg-surface px-2 py-1 text-sm text-white"
                placeholder="添加讨论…"
                value={generalComment}
                onChange={(e) => setGeneralComment(e.target.value)}
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
