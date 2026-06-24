import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { fetchGlobalStatus } from "../api/client";
import { useAuth } from "../context/AuthContext";
import { ProjectStatusPanel } from "../components/ProjectStatusPanel";
import { PhaseBadge } from "../components/PhaseStepper";

function AdminDashboard() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["status"],
    queryFn: () => fetchGlobalStatus(),
    refetchInterval: 30_000,
  });

  if (isLoading) return <p className="text-slate-400">加载看板…</p>;
  if (error) return <p className="text-red-400">加载失败，请检查 API Token 与服务状态</p>;
  if (!data) return null;

  return (
    <div className="space-y-8">
      <section>
        <h1 className="mb-4 text-2xl font-bold text-white">全局看板</h1>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-6">
          {Object.entries(data.total_experiments_by_phase).map(([phase, count]) => (
            <div key={phase} className="card text-center">
              <div className="mb-1 text-2xl font-semibold text-white">{count}</div>
              <PhaseBadge phase={phase as never} />
            </div>
          ))}
        </div>
      </section>

      <section>
        <h2 className="mb-3 text-lg font-semibold text-white">最近活动</h2>
        <div className="card overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="text-slate-500">
              <tr>
                <th className="pb-2 pr-4">实验</th>
                <th className="pb-2 pr-4">阶段</th>
                <th className="pb-2 pr-4">更新时间</th>
              </tr>
            </thead>
            <tbody>
              {data.recent_experiments.map((exp) => (
                <tr key={exp.id} className="border-t border-surface-border">
                  <td className="py-2 pr-4">
                    <Link to={`/experiments/${exp.id}`} className="text-accent hover:underline">
                      {exp.title}
                    </Link>
                  </td>
                  <td className="py-2 pr-4">
                    <PhaseBadge phase={exp.phase} />
                  </td>
                  <td className="py-2 text-slate-400">{new Date(exp.updated_at).toLocaleString()}</td>
                </tr>
              ))}
              {data.recent_experiments.length === 0 && (
                <tr>
                  <td colSpan={3} className="py-4 text-slate-500">
                    暂无实验
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      <section>
        <h2 className="mb-3 text-lg font-semibold text-white">项目</h2>
        <div className="grid gap-4 md:grid-cols-2">
          {data.projects.map((ps) => (
            <Link
              key={ps.project.id}
              to={`/projects/${ps.project.id}`}
              className="card block transition-colors hover:border-accent/50"
            >
              <div className="mb-1 flex items-center gap-2">
                <h3 className="font-semibold text-white">{ps.project.name}</h3>
                <span className="font-mono text-xs text-slate-500">{ps.project.project_key}</span>
              </div>
              <p className="mb-3 truncate text-xs text-slate-500">{ps.project.workspace_path}</p>
              <div className="flex flex-wrap gap-2 text-xs">
                {Object.entries(ps.experiment_counts_by_phase)
                  .filter(([, n]) => n > 0)
                  .map(([phase, count]) => (
                    <span key={phase} className="rounded bg-surface px-2 py-0.5 text-slate-300">
                      {phase}: {count}
                    </span>
                  ))}
              </div>
            </Link>
          ))}
          {data.projects.length === 0 && <p className="text-slate-500">暂无项目</p>}
        </div>
      </section>
    </div>
  );
}

export function StatusPage() {
  const { isAdmin, projectId } = useAuth();

  if (isAdmin) {
    return <AdminDashboard />;
  }

  if (!projectId) {
    return (
      <div className="card">
        <h1 className="mb-2 text-xl font-bold text-white">未绑定项目</h1>
        <p className="text-slate-400">
          当前 Agent 未绑定项目，请联系 Admin 注册并绑定 project_key。
        </p>
      </div>
    );
  }

  return <ProjectStatusPanel projectId={projectId} isAdmin={false} />;
}
