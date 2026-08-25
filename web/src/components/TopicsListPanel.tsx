import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { fetchTopics } from "../api/client";
import type { TopicStatus, TopicSummary } from "../api/types";
import { useDebouncedValue } from "../hooks/useDebouncedValue";
import { CopyableId } from "./CopyableId";
import { PaginationBar } from "./PaginationBar";

const TOPIC_STATUS_TABS: { label: string; value: TopicStatus | undefined }[] = [
  { label: "全部", value: undefined },
  { label: "进行中", value: "open" },
  { label: "已关闭", value: "closed" },
];

interface TopicsListPanelProps {
  projectId: string;
  pageSize?: number;
  title?: string;
  viewAllHref?: string;
  emptyLabel?: string;
}

export function TopicsListPanel({
  projectId,
  pageSize = 20,
  title = "话题",
  viewAllHref,
  emptyLabel = "暂无话题。用「发布话题」复制 map fs topic-create 命令",
}: TopicsListPanelProps) {
  const [statusFilter, setStatusFilter] = useState<TopicStatus | undefined>(undefined);
  const [searchInput, setSearchInput] = useState("");
  const [includeArchived, setIncludeArchived] = useState(false);
  const [page, setPage] = useState(1);
  const debouncedQ = useDebouncedValue(searchInput.trim(), 300);

  useEffect(() => {
    setPage(1);
  }, [statusFilter, debouncedQ, includeArchived]);

  const topicsQuery = useQuery({
    queryKey: ["topics", projectId, statusFilter, debouncedQ, includeArchived, page, pageSize],
    queryFn: () =>
      fetchTopics(projectId, {
        status: statusFilter,
        q: debouncedQ || undefined,
        page,
        pageSize,
        includeArchived,
      }),
  });

  const topics = topicsQuery.data?.items ?? [];
  const total = topicsQuery.data?.total ?? 0;

  return (
    <section className="card">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-lg font-semibold text-white">{title}</h2>
        <div className="flex items-center gap-3 text-xs text-slate-500">
          <span>共 {total} 个</span>
          {viewAllHref && (
            <Link to={viewAllHref} className="text-accent hover:underline">
              查看全部 →
            </Link>
          )}
        </div>
      </div>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        {TOPIC_STATUS_TABS.map((tab) => (
          <button
            key={tab.label}
            type="button"
            className={`btn-secondary py-1 text-xs ${statusFilter === tab.value ? "ring-1 ring-accent" : ""}`}
            onClick={() => setStatusFilter(tab.value)}
          >
            {tab.label}
          </button>
        ))}
        <label className="flex items-center gap-1 text-xs text-slate-400">
          <input
            type="checkbox"
            checked={includeArchived}
            onChange={(e) => setIncludeArchived(e.target.checked)}
          />
          显示已归档
        </label>
        <input
          type="search"
          value={searchInput}
          onChange={(e) => setSearchInput(e.target.value)}
          placeholder="搜索话题…"
          className="ml-auto min-w-[12rem] flex-1 rounded border border-surface-border bg-surface px-3 py-1 text-sm text-white placeholder:text-slate-500"
        />
      </div>
      {topicsQuery.isLoading ? (
        <p className="py-2 text-sm text-slate-500">加载话题…</p>
      ) : (
        <>
          <TopicList topics={topics} emptyLabel={emptyLabel} />
          <PaginationBar page={page} pageSize={pageSize} total={total} onPageChange={setPage} />
        </>
      )}
    </section>
  );
}

function TopicList({ topics, emptyLabel }: { topics: TopicSummary[]; emptyLabel?: string }) {
  if (topics.length === 0) return <p className="py-2 text-sm text-slate-500">{emptyLabel ?? "暂无话题"}</p>;
  return (
    <ul className="space-y-1">
      {topics.map((t) => (
        <li
          key={t.id}
          className="flex flex-wrap items-center justify-between gap-2 border-b border-surface-border py-2 last:border-0"
        >
          <Link to={`/topics/${t.id}`} className="text-accent hover:underline">
            {t.archived_at ? "📦 " : ""}
            {t.pinned ? "📌 " : ""}
            {t.title}
          </Link>
          <div className="flex items-center gap-3 text-xs text-slate-500">
            <CopyableId id={t.id} />
            <span>{t.creator_name ?? `${t.creator_agent_id.slice(0, 8)}…`}</span>
            <span
              className={`badge ${t.status === "open" ? "bg-emerald-900/40 text-emerald-200" : "bg-surface text-slate-400"}`}
            >
              {t.status === "open" ? "进行中" : "已关闭"}
            </span>
            {t.archived_at && <span className="text-amber-500/80">已归档</span>}
            <span>{t.comment_count} 评论</span>
            {(t.experiment_count ?? 0) > 0 && <span>{t.experiment_count ?? 0} 实验</span>}
          </div>
        </li>
      ))}
    </ul>
  );
}
