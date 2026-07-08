import { useEffect, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { createReview } from "../api/client";
import type { Review, ReviewItem, ReviewItemStatus } from "../api/types";

const STATUS_LABELS: Record<ReviewItemStatus, string> = {
  open: "待解决",
  addressed: "已修改",
  rebutted: "已反驳",
  resolved: "已认可",
  withdrawn: "已撤回",
  escalated: "已升级",
};

const STATUS_COLORS: Record<ReviewItemStatus, string> = {
  open: "bg-red-900/50 text-red-200",
  addressed: "bg-amber-900/50 text-amber-200",
  rebutted: "bg-orange-900/50 text-orange-200",
  resolved: "bg-emerald-900/50 text-emerald-200",
  withdrawn: "bg-slate-700 text-slate-300",
  escalated: "bg-purple-900/50 text-purple-200",
};

const ARCHIVED_REASON_LABELS: Record<string, string> = {
  auto: "自动归档（plan 修订）",
  manual: "手动归档",
  superseded: "已被新版覆盖",
};

function ArchivedReviewCard({ review }: { review: Review }) {
  const reason = review.archived_reason ?? "auto";
  const reasonLabel = ARCHIVED_REASON_LABELS[reason] ?? reason;
  const archivedAt = review.archived_at
    ? new Date(review.archived_at).toLocaleString()
    : "(pre-archive, all reviews shown)";
  return (
    <div
      className="rounded border border-slate-700/60 bg-slate-900/40 p-2 text-xs text-slate-400"
      data-testid="archived-review-card"
      data-archived-reason={reason}
    >
      <div className="mb-1 flex items-center justify-between gap-2">
        <span className="font-mono text-[10px] text-slate-500">
          plan v{review.plan_version} · {reasonLabel}
        </span>
        <span className="text-[10px] text-slate-600">{archivedAt}</span>
      </div>
      <ul className="space-y-1">
        {(review.items ?? []).map((item) => (
          <ItemRow key={item.id} item={item} />
        ))}
      </ul>
    </div>
  );
}

function ItemRow({ item }: { item: ReviewItem }) {
  if (item.kind === "reasonable") {
    return (
      <li className="flex gap-2 text-sm text-emerald-200/90">
        <span className="text-emerald-400">✓</span>
        <span>{item.content}</span>
      </li>
    );
  }

  const status = item.status ?? "open";
  return (
    <li className="rounded border border-surface-border bg-surface/50 p-2 text-sm">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <span className="text-red-200/90">{item.content}</span>
        <span className={`badge shrink-0 ${STATUS_COLORS[status]}`}>{STATUS_LABELS[status]}</span>
      </div>
      {item.waived_reason && (
        <div
          className="mt-2 whitespace-pre-wrap rounded border border-amber-700/40 bg-amber-950/30 p-2 text-xs text-amber-100/90"
          data-testid="review-item-waived-reason"
        >
          <span className="mb-1 block font-semibold text-amber-300">waived_reason:</span>
          {item.waived_reason}
        </div>
      )}
    </li>
  );
}

function splitLines(s: string): string[] {
  return s
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean);
}

interface ReviewSummaryProps {
  experimentId: string;
  reviews: Review[];
  onUpdated: () => void;
  canAddReview?: boolean;
  highlightReview?: boolean;
}

export function ReviewSummary({
  experimentId,
  reviews,
  onUpdated,
  canAddReview = false,
  highlightReview = false,
}: ReviewSummaryProps) {
  const [expanded, setExpanded] = useState(false);
  const [okText, setOkText] = useState("");
  const [badText, setBadText] = useState("");

  const reviewMutation = useMutation({
    mutationFn: () =>
      createReview(experimentId, {
        reasonable_items: splitLines(okText),
        unreasonable_items: splitLines(badText),
      }),
    onSuccess: () => {
      setOkText("");
      setBadText("");
      setExpanded(false);
      onUpdated();
    },
  });

  // I1(e): split reviews into active (canonical) and archived (collapsed by default).
  // archived_reason is non-null on every archived review; archived_at may be null
  // for the migration-034 backfill "pre-archive marker" rows. Main counts and
  // item lists come from active reviews only — archived reviews live under the
  // toggle section.
  const activeReviews = reviews.filter((r) => r.archived_reason == null);
  const archivedReviews = reviews.filter((r) => r.archived_reason != null);
  const reasonable = activeReviews.flatMap((r) => (r.items ?? []).filter((i) => i.kind === "reasonable"));
  const unreasonable = activeReviews.flatMap((r) => (r.items ?? []).filter((i) => i.kind === "unreasonable"));
  const openCount = unreasonable.filter(
    (i) => i.status && ["open", "addressed", "rebutted", "escalated"].includes(i.status)
  ).length;
  const [showArchived, setShowArchived] = useState(false);

  useEffect(() => {
    if (highlightReview && canAddReview) {
      setExpanded(true);
    }
  }, [highlightReview, canAddReview]);

  return (
    <div className={`card h-full ${highlightReview ? "ring-2 ring-accent" : ""}`}>
      <h2 className="mb-3 text-base font-semibold text-white">评审摘要</h2>
      <div className="mb-4 grid grid-cols-2 gap-2 text-center text-sm">
        <div className="rounded bg-emerald-950/40 py-2">
          <div className="text-lg font-semibold text-emerald-300">{reasonable.length}</div>
          <div className="text-slate-400">合理项</div>
        </div>
        <div className="rounded bg-red-950/40 py-2">
          <div className="text-lg font-semibold text-red-300">{unreasonable.length}</div>
          <div className="text-slate-400">不合理项 {openCount > 0 && `(${openCount} open)`}</div>
        </div>
      </div>
      {unreasonable.length > 0 && (
        <div className="mb-3">
          <h3 className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-500">不合理项</h3>
          <ul className="space-y-2">
            {unreasonable.map((item) => (
              <ItemRow key={item.id} item={item} />
            ))}
          </ul>
        </div>
      )}
      {reasonable.length > 0 && (
        <div>
          <h3 className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-500">合理项</h3>
          <ul className="space-y-1">
            {reasonable.map((item) => (
              <ItemRow key={item.id} item={item} />
            ))}
          </ul>
        </div>
      )}
      {reviews.length === 0 && <p className="text-sm text-slate-500">暂无评审</p>}

      {archivedReviews.length > 0 && (
        <div className="mt-4 border-t border-surface-border pt-3" data-testid="archived-reviews-section">
          <button
            type="button"
            className="flex w-full items-center justify-between text-left text-xs font-medium uppercase tracking-wide text-slate-500 hover:text-slate-300"
            onClick={() => setShowArchived((v) => !v)}
            data-testid="toggle-archived-reviews"
            aria-expanded={showArchived}
          >
            <span>
              {showArchived ? "▼" : "▶"} 已归档评审 ({archivedReviews.length})
            </span>
            <span className="text-[10px] normal-case text-slate-600">
              仅供历史回溯，不可修改
            </span>
          </button>
          {showArchived && (
            <div className="mt-2 space-y-2" data-testid="archived-reviews-list">
              {archivedReviews.map((review) => (
                <ArchivedReviewCard key={review.id} review={review} />
              ))}
            </div>
          )}
        </div>
      )}
      {canAddReview && (
        <div className="mt-4 border-t border-surface-border pt-3">
          {expanded ? (
            <div className="space-y-2">
              <div>
                <label className="mb-1 block text-xs text-slate-400">合理项（每行一条，可选）</label>
                <textarea
                  className="min-h-[60px] w-full rounded border border-surface-border bg-surface px-2 py-1 text-sm text-white"
                  value={okText}
                  onChange={(e) => setOkText(e.target.value)}
                />
              </div>
              <div>
                <label className="mb-1 block text-xs text-slate-400">不合理项（每行一条，可选）</label>
                <textarea
                  className="min-h-[60px] w-full rounded border border-surface-border bg-surface px-2 py-1 text-sm text-white"
                  value={badText}
                  onChange={(e) => setBadText(e.target.value)}
                />
              </div>
              {reviewMutation.isError && <p className="text-sm text-red-400">提交失败</p>}
              <div className="flex justify-end gap-2">
                <button type="button" className="btn-secondary" onClick={() => setExpanded(false)}>
                  取消
                </button>
                <button
                  type="button"
                  className="btn-primary"
                  disabled={reviewMutation.isPending || (!okText.trim() && !badText.trim())}
                  onClick={() => reviewMutation.mutate()}
                >
                  {reviewMutation.isPending ? "提交中…" : "提交评审"}
                </button>
              </div>
            </div>
          ) : (
            <button type="button" className="btn-secondary" onClick={() => setExpanded(true)}>
              提交评审
            </button>
          )}
        </div>
      )}
    </div>
  );
}

export function getUnreasonableItems(reviews: Review[]) {
  return reviews.flatMap((r) => (r.items ?? []).filter((i) => i.kind === "unreasonable"));
}
