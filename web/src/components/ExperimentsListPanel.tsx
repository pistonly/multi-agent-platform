import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { fetchProjectExperiments } from "../api/client";
import type { ExperimentPhase, ExperimentSummary } from "../api/types";
import { useDebouncedValue } from "../hooks/useDebouncedValue";
import { PaginationBar } from "./PaginationBar";
import { PhaseBadge } from "./PhaseStepper";

const EXPERIMENT_PHASE_OPTIONS: { label: string; value: ExperimentPhase | undefined }[] = [
  { label: "全部", value: undefined },
  { label: "draft", value: "draft" },
  { label: "review", value: "review" },
  { label: "approved", value: "approved" },
  { label: "running", value: "running" },
  { label: "result_review", value: "result_review" },
  { label: "done", value: "done" },
  { label: "cancelled", value: "cancelled" },
];

interface ExperimentsListPanelProps {
  projectId: string;
  pageSize?: number;
  title?: string;
  viewAllHref?: string;
  emptyLabel?: string;
}

export function ExperimentsListPanel({
  projectId,
  pageSize = 20,
  title = "实验列表",
  viewAllHref,
  emptyLabel = "暂无实验",
}: ExperimentsListPanelProps) {
  const [phaseFilter, setPhaseFilter] = useState<ExperimentPhase | undefined>(undefined);
  const [searchInput, setSearchInput] = useState("");
  const [includeArchived, setIncludeArchived] = useState(false);
  const [page, setPage] = useState(1);
  const debouncedQ = useDebouncedValue(searchInput.trim(), 300);

  useEffect(() => {
    setPage(1);
  }, [phaseFilter, debouncedQ, includeArchived]);

  const experimentsQuery = useQuery({
    queryKey: ["project-experiments", projectId, phaseFilter, debouncedQ, includeArchived, page, pageSize],
    queryFn: () =>
      fetchProjectExperiments(projectId, {
        phase: phaseFilter,
        q: debouncedQ || undefined,
        page,
        pageSize,
        includeArchived,
      }),
  });

  const experiments = experimentsQuery.data?.items ?? [];
  const total = experimentsQuery.data?.total ?? 0;

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
        <select
          value={phaseFilter ?? ""}
          onChange={(e) => setPhaseFilter((e.target.value || undefined) as ExperimentPhase | undefined)}
          className="rounded border border-surface-border bg-surface px-3 py-1 text-sm text-white"
        >
          {EXPERIMENT_PHASE_OPTIONS.map((opt) => (
            <option key={opt.label} value={opt.value ?? ""}>
              {opt.label === "全部" ? "全部阶段" : opt.label}
            </option>
          ))}
        </select>
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
          placeholder="搜索实验…"
          className="min-w-[12rem] flex-1 rounded border border-surface-border bg-surface px-3 py-1 text-sm text-white placeholder:text-slate-500"
        />
      </div>
      {experimentsQuery.isLoading ? (
        <p className="py-2 text-sm text-slate-500">加载实验…</p>
      ) : (
        <>
          <ExperimentTable experiments={experiments} emptyLabel={emptyLabel} />
          <PaginationBar page={page} pageSize={pageSize} total={total} onPageChange={setPage} />
        </>
      )}
    </section>
  );
}

function ExperimentTable({
  experiments,
  emptyLabel = "暂无数据",
}: {
  experiments: Pick<ExperimentSummary, "id" | "title" | "phase" | "current_plan_version" | "updated_at" | "archived_at">[];
  emptyLabel?: string;
}) {
  return (
    <table className="w-full text-left text-sm">
      <thead className="text-slate-500">
        <tr>
          <th className="pb-2 pr-4">标题</th>
          <th className="pb-2 pr-4">阶段</th>
          <th className="pb-2 pr-4">计划</th>
          <th className="pb-2">更新</th>
        </tr>
      </thead>
      <tbody>
        {experiments.map((exp) => (
          <tr key={exp.id} className="border-t border-surface-border">
            <td className="py-2 pr-4">
              <Link to={`/experiments/${exp.id}`} className="text-accent hover:underline">
                {exp.archived_at ? "📦 " : ""}
                {exp.title}
              </Link>
            </td>
            <td className="py-2 pr-4">
              <PhaseBadge phase={exp.phase} />
            </td>
            <td className="py-2 pr-4 text-slate-400">v{exp.current_plan_version}</td>
            <td className="py-2 text-slate-400">{new Date(exp.updated_at).toLocaleString()}</td>
          </tr>
        ))}
        {experiments.length === 0 && (
          <tr>
            <td colSpan={4} className="py-4 text-slate-500">
              {emptyLabel}
            </td>
          </tr>
        )}
      </tbody>
    </table>
  );
}
