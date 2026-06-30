import { useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { Agent, AgentRole } from "../api/types";
import { useAgents } from "../hooks/useAgents";
import { useAuth } from "../context/AuthContext";

type RoleFilter = "all" | AgentRole;

const ROLE_FILTERS: { value: RoleFilter; label: string }[] = [
  { value: "all", label: "全部" },
  { value: "admin", label: "Admin" },
  { value: "agent", label: "Agent" },
];

function describeAgent(agent: Agent): string {
  return [
    `name: ${agent.name}`,
    `role: ${agent.role}`,
    `project: ${agent.project_key ?? "—"}`,
    `id: ${agent.id}`,
    `created_at: ${agent.created_at}`,
  ].join("\n");
}

export function AgentsPage() {
  const { agent: me, isAdmin } = useAuth();
  const queryClient = useQueryClient();
  const { agents, isLoading, error, refetch } = useAgents();
  const [roleFilter, setRoleFilter] = useState<RoleFilter>("all");
  const [search, setSearch] = useState("");

  const filtered = useMemo(() => {
    const term = search.trim().toLowerCase();
    return agents.filter((a) => {
      if (roleFilter !== "all" && a.role !== roleFilter) return false;
      if (!term) return true;
      return (
        a.name.toLowerCase().includes(term) ||
        (a.project_key ?? "").toLowerCase().includes(term) ||
        a.id.toLowerCase().includes(term)
      );
    });
  }, [agents, roleFilter, search]);

  function handleRefresh() {
    queryClient.invalidateQueries({ queryKey: ["agents"] });
    refetch();
  }

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-white">Agents</h1>
          <p className="mt-1 text-sm text-slate-400">
            当前会话：<span className="font-mono text-slate-200">{me?.name ?? "未登录"}</span>
            {me?.role && (
              <span className="ml-2 rounded bg-surface px-1.5 py-0.5 text-xs text-slate-300">
                role={me.role}
              </span>
            )}
            {me?.project_key && (
              <span className="ml-2 rounded bg-surface px-1.5 py-0.5 text-xs text-slate-300">
                project={me.project_key}
              </span>
            )}
            {!isAdmin && (
              <span className="ml-2 text-xs text-slate-500">
                （仅展示同项目与 Admin；详情见管理后台）
              </span>
            )}
          </p>
        </div>
        <button type="button" className="btn-secondary" onClick={handleRefresh}>
          刷新
        </button>
      </header>

      <div className="card space-y-4">
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex items-center gap-1 rounded border border-surface-border bg-surface/40 p-1 text-xs">
            {ROLE_FILTERS.map((f) => (
              <button
                key={f.value}
                type="button"
                onClick={() => setRoleFilter(f.value)}
                className={`rounded px-2 py-1 ${
                  roleFilter === f.value ? "bg-accent text-white" : "text-slate-300 hover:bg-surface"
                }`}
              >
                {f.label}
              </button>
            ))}
          </div>
          <input
            type="search"
            placeholder="按名称 / 项目 / ID 过滤"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="min-w-[220px] flex-1 rounded border border-surface-border bg-surface px-3 py-1.5 text-sm text-white"
          />
          <span className="text-xs text-slate-500">共 {filtered.length} / {agents.length}</span>
        </div>

        {isLoading && <p className="text-sm text-slate-400">加载中…</p>}
        {error && <p className="text-sm text-red-400">加载失败</p>}

        {!isLoading && !error && (
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead className="text-left text-xs uppercase text-slate-500">
                <tr>
                  <th className="py-2 pr-4">名称</th>
                  <th className="py-2 pr-4">角色</th>
                  <th className="py-2 pr-4">项目</th>
                  <th className="py-2 pr-4">ID</th>
                  <th className="py-2 pr-4">创建时间</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-surface-border">
                {filtered.length === 0 && (
                  <tr>
                    <td colSpan={5} className="py-6 text-center text-slate-500">
                      没有匹配的 Agent
                    </td>
                  </tr>
                )}
                {filtered.map((a) => (
                  <tr key={a.id} className="text-slate-200">
                    <td className="py-2 pr-4 font-medium text-white" title={describeAgent(a)}>
                      {a.name}
                      {me?.id === a.id && (
                        <span className="ml-2 rounded bg-emerald-900/40 px-1.5 py-0.5 text-[10px] text-emerald-200">
                          你
                        </span>
                      )}
                    </td>
                    <td className="py-2 pr-4">
                      <span
                        className={`badge ${
                          a.role === "admin"
                            ? "bg-accent-muted text-accent"
                            : "bg-surface text-slate-300"
                        }`}
                      >
                        {a.role}
                      </span>
                    </td>
                    <td className="py-2 pr-4 font-mono text-xs text-slate-400">
                      {a.project_key ?? "—"}
                    </td>
                    <td className="py-2 pr-4 font-mono text-xs text-slate-500">{a.id}</td>
                    <td className="py-2 pr-4 text-xs text-slate-500">
                      {new Date(a.created_at).toLocaleString()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
