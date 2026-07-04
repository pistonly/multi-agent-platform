import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { fetchFeedbacks, submitFeedback, updateFeedback } from "../api/client";
import type { FeedbackCategory, FeedbackStatus, PlatformFeedback } from "../api/types";
import { useAuth } from "../context/AuthContext";

const INPUT_CLASS =
  "w-full rounded border border-surface-border bg-surface px-3 py-2 text-sm text-white";

const STATUS_OPTIONS: FeedbackStatus[] = ["new", "triaged", "in_progress", "resolved"];
const CATEGORY_OPTIONS: FeedbackCategory[] = ["bug", "suggestion", "question", "other"];

const STATUS_LABEL: Record<FeedbackStatus, string> = {
  new: "新",
  triaged: "已分类",
  in_progress: "处理中",
  resolved: "已解决",
};

type TriagePatch = { status?: FeedbackStatus; category?: FeedbackCategory | null };

export function FeedbackPage() {
  const { isAdmin } = useAuth();
  const queryClient = useQueryClient();
  const queryKey = ["feedbacks"] as const;

  const [body, setBody] = useState("");
  const [category, setCategory] = useState<FeedbackCategory | "">("");
  const [submitted, setSubmitted] = useState(false);

  const submitMutation = useMutation({
    mutationFn: () => submitFeedback({ body: body.trim(), category: category || null }),
    onSuccess: () => {
      setBody("");
      setCategory("");
      setSubmitted(true);
      queryClient.invalidateQueries({ queryKey });
    },
  });

  const feedbackQuery = useQuery({
    queryKey: queryKey,
    queryFn: () => fetchFeedbacks({ pageSize: 100 }),
    enabled: isAdmin,
  });

  const updateMutation = useMutation({
    mutationFn: (vars: { id: string } & TriagePatch) =>
      updateFeedback(vars.id, { status: vars.status, category: vars.category }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey }),
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-white">反馈信箱</h1>
        <p className="mt-1 text-sm text-slate-400">
          遇到问题或有建议？在此提交，平台维护者会查看并跟进。
        </p>
      </div>

      <section className="card space-y-3">
        <h2 className="text-lg font-semibold text-white">提交反馈</h2>
        <textarea
          className={`min-h-[96px] ${INPUT_CLASS}`}
          placeholder="描述你遇到的问题或建议…"
          value={body}
          onChange={(e) => {
            setBody(e.target.value);
            setSubmitted(false);
          }}
        />
        <div className="flex flex-wrap items-center gap-3">
          <select
            className={INPUT_CLASS}
            style={{ width: "auto" }}
            value={category}
            onChange={(e) => setCategory(e.target.value as FeedbackCategory | "")}
          >
            <option value="">类别（可选）</option>
            {CATEGORY_OPTIONS.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
          <button
            type="button"
            className="btn-primary"
            disabled={!body.trim() || submitMutation.isPending}
            onClick={() => submitMutation.mutate()}
          >
            {submitMutation.isPending ? "提交中…" : "提交"}
          </button>
          {submitMutation.isError && <span className="text-xs text-red-400">提交失败</span>}
          {submitted && <span className="text-xs text-emerald-400">已提交，感谢反馈！</span>}
        </div>
      </section>

      {isAdmin && (
        <section className="space-y-3">
          <h2 className="text-lg font-semibold text-white">
            收件箱{feedbackQuery.data ? `（${feedbackQuery.data.total}）` : ""}
          </h2>
          {feedbackQuery.isLoading ? (
            <p className="text-slate-400">加载…</p>
          ) : (feedbackQuery.data?.items.length ?? 0) === 0 ? (
            <p className="text-sm text-slate-500">暂无反馈</p>
          ) : (
            <ul className="space-y-2">
              {feedbackQuery.data?.items.map((fb) => (
                <FeedbackRow
                  key={fb.id}
                  feedback={fb}
                  onTriage={(id, patch) => updateMutation.mutate({ id, ...patch })}
                />
              ))}
            </ul>
          )}
        </section>
      )}
    </div>
  );
}

function FeedbackRow({
  feedback,
  onTriage,
}: {
  feedback: PlatformFeedback;
  onTriage: (id: string, patch: TriagePatch) => void;
}) {
  return (
    <li className="card space-y-2">
      <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
        <span>{new Date(feedback.created_at).toLocaleString()}</span>
        {feedback.author_name && (
          <span className="badge bg-surface text-slate-400">{feedback.author_name}</span>
        )}
        <span className="badge bg-accent-muted text-accent">{STATUS_LABEL[feedback.status]}</span>
        {feedback.category && (
          <span className="badge bg-surface text-slate-400">{feedback.category}</span>
        )}
      </div>
      <p className="whitespace-pre-wrap text-sm text-slate-200">{feedback.body}</p>
      <div className="flex flex-wrap items-center gap-2">
        <select
          className={`${INPUT_CLASS} py-1`}
          style={{ width: "auto" }}
          value={feedback.status}
          onChange={(e) => onTriage(feedback.id, { status: e.target.value as FeedbackStatus })}
        >
          {STATUS_OPTIONS.map((s) => (
            <option key={s} value={s}>
              {STATUS_LABEL[s]}
            </option>
          ))}
        </select>
        <select
          className={`${INPUT_CLASS} py-1`}
          style={{ width: "auto" }}
          value={feedback.category ?? ""}
          onChange={(e) =>
            onTriage(feedback.id, {
              category: (e.target.value || null) as FeedbackCategory | null,
            })
          }
        >
          <option value="">未分类</option>
          {CATEGORY_OPTIONS.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
      </div>
    </li>
  );
}
