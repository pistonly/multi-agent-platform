import { useMutation, useQueryClient } from "@tanstack/react-query";
import axios from "axios";
import { useState } from "react";
import { createExperiment, formatApiError } from "../api/client";
import type { ExperimentSummary } from "../api/types";
import {
  PLAN_FRONTMATTER_TEMPLATE,
  wrapPlanWithFrontmatter,
} from "../utils/planFrontmatter";

interface CreateExperimentFormProps {
  projectId: string;
  topicId?: string | null;
  onCreated?: (experiment: ExperimentSummary) => void;
  onCancel?: () => void;
}

export function CreateExperimentForm({
  projectId,
  topicId,
  onCreated,
  onCancel,
}: CreateExperimentFormProps) {
  const queryClient = useQueryClient();
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [plan, setPlan] = useState(PLAN_FRONTMATTER_TEMPLATE);
  const [submitForReview, setSubmitForReview] = useState(false);

  const createMutation = useMutation({
    mutationFn: () =>
      createExperiment(projectId, {
        title: title.trim(),
        description: description.trim() || null,
        plan: { content_md: wrapPlanWithFrontmatter(plan, title) },
        submit_for_review: submitForReview,
        topic_id: topicId ?? null,
      }),
    onSuccess: (exp) => {
      queryClient.invalidateQueries({ queryKey: ["project-status", projectId] });
      queryClient.invalidateQueries({ queryKey: ["project-experiments", projectId] });
      queryClient.invalidateQueries({ queryKey: ["status"] });
      if (topicId) queryClient.invalidateQueries({ queryKey: ["topic", topicId] });
      onCreated?.(exp);
    },
  });

  return (
    <form
      className="space-y-3"
      onSubmit={(e) => {
        e.preventDefault();
        createMutation.mutate();
      }}
    >
      <div>
        <label htmlFor="create-experiment-title" className="mb-1 block text-xs text-slate-400">
          标题
        </label>
        <input
          id="create-experiment-title"
          required
          className="w-full rounded border border-surface-border bg-surface px-3 py-2 text-sm text-white"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
        />
      </div>
      <div>
        <label htmlFor="create-experiment-description" className="mb-1 block text-xs text-slate-400">
          描述（可选）
        </label>
        <input
          id="create-experiment-description"
          className="w-full rounded border border-surface-border bg-surface px-3 py-2 text-sm text-white"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
      </div>
      <div>
        <label htmlFor="create-experiment-plan" className="mb-1 block text-xs text-slate-400">
          实验计划（Markdown）
        </label>
        <p className="mb-1 text-xs text-slate-500">
          必须以 YAML frontmatter 开头（title / acceptance / evidence_keys / dependencies）。缺这四项服务端会 422。
        </p>
        <textarea
          id="create-experiment-plan"
          required
          className="min-h-[220px] w-full rounded border border-surface-border bg-surface px-3 py-2 font-mono text-sm text-white"
          value={plan}
          onChange={(e) => setPlan(e.target.value)}
          placeholder={PLAN_FRONTMATTER_TEMPLATE}
        />
      </div>
      <label className="flex items-center gap-2 text-sm text-slate-300">
        <input
          type="checkbox"
          checked={submitForReview}
          onChange={(e) => setSubmitForReview(e.target.checked)}
        />
        创建后直接提交评审
      </label>
      {topicId && <p className="text-xs text-slate-500">将自动关联到当前话题</p>}
      {createMutation.isError && (
        <p className="whitespace-pre-wrap text-sm text-red-400">
          {axios.isAxiosError(createMutation.error)
            ? formatApiError(createMutation.error)
            : "创建失败，请检查权限与输入"}
        </p>
      )}
      <div className="flex justify-end gap-2 pt-1">
        {onCancel && (
          <button type="button" className="btn-secondary" onClick={onCancel}>
            取消
          </button>
        )}
        <button type="submit" className="btn-primary" disabled={createMutation.isPending}>
          {createMutation.isPending ? "创建中…" : "发布实验"}
        </button>
      </div>
    </form>
  );
}
