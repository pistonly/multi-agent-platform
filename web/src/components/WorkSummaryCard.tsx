import type { AgentWorkSummary, SummaryBucket, SummaryBucketItem } from "../api/types";

const BUCKET_LABELS: Record<SummaryBucket["kind"], string> = {
  mention: "@提及",
  round_ack: "Round Ack",
  pending_reply: "待回复",
  explicit_only: "显式待办",
  informational_only: "信息项",
  action_items: "行动项",
};

const BUCKET_ORDER: SummaryBucket["kind"][] = [
  "mention",
  "round_ack",
  "pending_reply",
  "explicit_only",
  "informational_only",
  "action_items",
];

const VISIBILITY_LABELS: Record<NonNullable<SummaryBucket["visibility"]>, string> = {
  all: "全员可见",
  host_only: "仅 host",
  reviewer_only: "仅 reviewer",
  participant_only: "仅 participant",
};

interface WorkSummaryCardProps {
  summary: AgentWorkSummary;
}

function BucketRow({ bucket }: { bucket: SummaryBucket }) {
  const items = bucket.items ?? [];
  return (
    <div
      className="rounded border border-surface-border bg-surface/50 p-3"
      data-testid={`work-summary-bucket-${bucket.kind}`}
    >
      <div className="mb-2 flex items-baseline justify-between">
        <div className="flex items-baseline gap-2">
          <span className="text-sm font-semibold text-white">
            {BUCKET_LABELS[bucket.kind]}
          </span>
          <span className="font-mono text-xs text-slate-500">
            {bucket.kind}
          </span>
        </div>
        <div className="flex items-center gap-2 text-xs text-slate-400">
          {bucket.visibility && (
            <span className="rounded bg-slate-800 px-2 py-0.5">
              {VISIBILITY_LABELS[bucket.visibility]}
            </span>
          )}
          <span className="font-mono text-base text-accent">{bucket.count}</span>
        </div>
      </div>
      {bucket.top_excerpt && (
        <p className="mb-2 line-clamp-2 text-xs text-slate-400">
          {bucket.top_excerpt}
        </p>
      )}
      {items.length > 0 && (
        <ul className="space-y-1">
          {items.map((item, idx) => (
            <ItemRow key={idx} item={item} />
          ))}
        </ul>
      )}
    </div>
  );
}

function ItemRow({ item }: { item: SummaryBucketItem }) {
  const title = item.topic_title ?? item.kind;
  return (
    <li
      className="flex items-baseline justify-between gap-2 text-xs text-slate-300"
      data-testid="work-summary-bucket-item"
    >
      <span className="truncate">{title}</span>
      {item.excerpt && (
        <span className="shrink-0 font-mono text-slate-500">{item.excerpt}</span>
      )}
    </li>
  );
}

export function WorkSummaryCard({ summary }: WorkSummaryCardProps) {
  const buckets = (summary.buckets ?? []).slice().sort((a, b) => {
    return BUCKET_ORDER.indexOf(a.kind) - BUCKET_ORDER.indexOf(b.kind);
  });
  const truncatedTopics = summary.topics_truncated ?? 0;
  const truncatedExperiments = summary.experiments_truncated ?? 0;
  const filtered = summary.visibility_filter_applied ?? false;

  return (
    <div className="card" data-testid="work-summary-card">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-base font-semibold text-white">待办摘要</h2>
        <div className="flex flex-wrap items-center gap-2 text-xs text-slate-400">
          <span className="rounded bg-surface px-2 py-0.5">
            话题 {summary.topics_needing_attention ?? 0}
          </span>
          <span className="rounded bg-surface px-2 py-0.5">
            实验 {summary.experiments_needing_attention ?? 0}
          </span>
          {filtered && (
            <span
              className="rounded bg-amber-900/40 px-2 py-0.5 text-amber-200"
              data-testid="work-summary-persona-filtered"
            >
              persona 已过滤
            </span>
          )}
          {(truncatedTopics > 0 || truncatedExperiments > 0) && (
            <span
              className="rounded bg-amber-900/40 px-2 py-0.5 text-amber-200"
              data-testid="work-summary-truncated"
            >
              截断 +{truncatedTopics + truncatedExperiments}
            </span>
          )}
        </div>
      </div>

      {buckets.length === 0 ? (
        <p className="text-sm text-slate-500" data-testid="work-summary-empty">
          暂无待办
        </p>
      ) : (
        <div className="grid gap-2 sm:grid-cols-2">
          {buckets.map((bucket) => (
            <BucketRow key={bucket.kind} bucket={bucket} />
          ))}
        </div>
      )}
    </div>
  );
}
