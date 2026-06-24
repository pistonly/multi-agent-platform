import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { fetchProjectStatus, fetchProjectStatusVersions } from "../api/client";
import { PhaseBadge } from "./PhaseStepper";
import { StatusEditor } from "./StatusEditor";
import { StatusMarkdown } from "./StatusMarkdown";

interface ProjectStatusPanelProps {
  projectId: string;
  isAdmin: boolean;
  showHeader?: boolean;
}

export function ProjectStatusPanel({ projectId, isAdmin, showHeader = true }: ProjectStatusPanelProps) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["project-status", projectId],
    queryFn: () => fetchProjectStatus(projectId),
    refetchInterval: 30_000,
  });

  if (isLoading) return <p className="text-slate-400">加载项目状态…</p>;
  if (error || !data) {
    return <p className="text-red-400">项目不存在或无权访问</p>;
  }

  const { project, experiment_counts_by_phase, active_experiments, recent_experiments, status_md } =
    data;

  return (
    <div className="space-y-8">
      {showHeader && (
        <div>
          <h1 className="text-2xl font-bold text-white">{project.name}</h1>
          <p className="mt-1 font-mono text-sm text-slate-400">
            {project.project_key} · {project.workspace_path}
          </p>
          {project.description && <p className="mt-2 text-slate-300">{project.description}</p>}
        </div>
      )}

      <section>
        <h2 className="mb-3 text-lg font-semibold text-white">实验快照</h2>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-6">
          {Object.entries(experiment_counts_by_phase).map(([phase, count]) => (
            <div key={phase} className="card text-center">
              <div className="mb-1 text-2xl font-semibold text-white">{count}</div>
              <PhaseBadge phase={phase as never} />
            </div>
          ))}
        </div>
      </section>

      <section className="card">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-white">Current Status</h2>
          <span className="text-xs text-slate-500">
            v{data.status_version}
            {data.status_updated_at &&
              ` · 更新于 ${new Date(data.status_updated_at).toLocaleString()}`}
          </span>
        </div>
        {status_md ? (
          <StatusMarkdown content={status_md} />
        ) : (
          <p className="text-slate-500">暂无 Status 文档</p>
        )}
      </section>

      {isAdmin && (
        <section className="card">
          <StatusEditor
            projectId={projectId}
            currentContent={status_md ?? ""}
            currentVersion={data.status_version}
          />
        </section>
      )}

      {!isAdmin && <StatusVersionHistory projectId={projectId} />}

      {active_experiments.length > 0 && (
        <section className="card">
          <h2 className="mb-3 text-lg font-semibold text-white">活跃实验</h2>
          <ExperimentTable experiments={active_experiments} />
        </section>
      )}

      <section className="card">
        <h2 className="mb-3 text-lg font-semibold text-white">实验列表</h2>
        <ExperimentTable experiments={recent_experiments} emptyLabel="暂无实验" />
      </section>
    </div>
  );
}

function ExperimentTable({
  experiments,
  emptyLabel = "暂无数据",
}: {
  experiments: { id: string; title: string; phase: string; current_plan_version: number; updated_at: string }[];
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
                {exp.title}
              </Link>
            </td>
            <td className="py-2 pr-4">
              <PhaseBadge phase={exp.phase as never} />
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

function StatusVersionHistory({ projectId }: { projectId: string }) {
  const { data: versions, isLoading } = useQuery({
    queryKey: ["project-status-versions", projectId],
    queryFn: () => fetchProjectStatusVersions(projectId),
  });

  if (isLoading) return null;
  if (!versions?.length) return null;

  return (
    <section className="card">
      <h2 className="mb-3 text-lg font-semibold text-white">版本历史</h2>
      <ul className="space-y-1 text-sm">
        {versions.map((v) => (
          <li key={v.id} className="flex justify-between text-slate-400">
            <span>
              v{v.version}
              {v.change_note && <span className="ml-2 text-slate-500">— {v.change_note}</span>}
            </span>
            <span className="text-xs">{new Date(v.created_at).toLocaleString()}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
