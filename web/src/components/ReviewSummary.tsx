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
    </li>
  );
}

export function ReviewSummary({ reviews }: { reviews: Review[] }) {
  const reasonable = reviews.flatMap((r) => r.items.filter((i) => i.kind === "reasonable"));
  const unreasonable = reviews.flatMap((r) => r.items.filter((i) => i.kind === "unreasonable"));
  const openCount = unreasonable.filter(
    (i) => i.status && ["open", "addressed", "rebutted", "escalated"].includes(i.status)
  ).length;

  return (
    <div className="card h-full">
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
    </div>
  );
}

export function getUnreasonableItems(reviews: Review[]) {
  return reviews.flatMap((r) => r.items.filter((i) => i.kind === "unreasonable"));
}
