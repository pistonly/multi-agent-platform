import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { fetchProject, fetchProjectExperiments } from "../api/client";
import { PhaseBadge } from "../components/PhaseStepper";

export function ProjectPage() {
  const { projectId } = useParams<{ projectId: string }>();

  const projectQuery = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => fetchProject(projectId!),
    enabled: !!projectId,
  });

  const experimentsQuery = useQuery({
    queryKey: ["experiments", projectId],
    queryFn: () => fetchProjectExperiments(projectId!),
    enabled: !!projectId,
  });

  if (projectQuery.isLoading) return <p className="text-slate-400">加载项目…</p>;
  if (projectQuery.error || !projectQuery.data) {
    return <p className="text-red-400">项目不存在或加载失败</p>;
  }

  const project = projectQuery.data;
  const experiments = experimentsQuery.data ?? [];

  return (
    <div className="space-y-6">
      <div>
        <Link to="/" className="text-sm text-slate-500 hover:text-white">
          ← 看板
        </Link>
        <h1 className="mt-2 text-2xl font-bold text-white">{project.name}</h1>
        <p className="mt-1 font-mono text-sm text-slate-400">{project.workspace_path}</p>
        {project.description && <p className="mt-2 text-slate-300">{project.description}</p>}
      </div>

      <section className="card">
        <h2 className="mb-3 text-lg font-semibold text-white">实验列表</h2>
        <table className="w-full text-left text-sm">
          <thead className="text-slate-500">
            <tr>
              <th className="pb-2">标题</th>
              <th className="pb-2">阶段</th>
              <th className="pb-2">计划版本</th>
              <th className="pb-2">更新</th>
            </tr>
          </thead>
          <tbody>
            {experiments.map((exp) => (
              <tr key={exp.id} className="border-t border-surface-border">
                <td className="py-2">
                  <Link to={`/experiments/${exp.id}`} className="text-accent hover:underline">
                    {exp.title}
                  </Link>
                </td>
                <td className="py-2">
                  <PhaseBadge phase={exp.phase} />
                </td>
                <td className="py-2 text-slate-400">v{exp.current_plan_version}</td>
                <td className="py-2 text-slate-400">{new Date(exp.updated_at).toLocaleString()}</td>
              </tr>
            ))}
            {experiments.length === 0 && (
              <tr>
                <td colSpan={4} className="py-4 text-slate-500">
                  暂无实验
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </section>
    </div>
  );
}
