import { useState } from "react";
import { Link, Outlet, Navigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { fetchWork } from "../api/client";
import type { AuthIdentity } from "../context/authIdentities";
import { useAuth } from "../context/AuthContext";
import { useNotificationStream } from "../hooks/useNotificationStream";
import { sumTodos } from "../utils/todoCount";
import { CreateProjectForm } from "./CreateProjectForm";

export function Layout() {
  const {
    token,
    agentName,
    role,
    projectKey,
    isAdmin,
    isReady,
    identities,
    activeIdentityId,
    switchIdentity,
    clearToken,
  } = useAuth();
  const [showCreateProject, setShowCreateProject] = useState(false);
  const [showIdentityMenu, setShowIdentityMenu] = useState(false);

  useNotificationStream(token, isReady && !!token);

  const workQuery = useQuery({
    queryKey: ["work"],
    queryFn: fetchWork,
    enabled: isReady && !!token,
    refetchInterval: 120_000,
  });
  const todoCount = workQuery.data ? sumTodos(workQuery.data.todos) : 0;
  const unreadCount = workQuery.data?.notifications.unread_count ?? 0;

  if (!isReady) {
    return (
      <div className="flex min-h-screen items-center justify-center text-slate-400">加载中…</div>
    );
  }

  if (!token) {
    return <Navigate to="/settings" replace />;
  }

  return (
    <div className="min-h-screen">
      <header className="border-b border-surface-border bg-surface-raised/80 backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-3">
          <div className="flex items-center gap-6">
            <Link to="/" className="text-lg font-semibold text-white">
              MAP
            </Link>
            <nav className="flex gap-4 text-sm text-slate-300">
              <Link to="/" className="hover:text-white">
                {isAdmin ? "看板" : "Current Status"}
              </Link>
              <Link to="/todos" className="relative hover:text-white">
                待办
                {todoCount > 0 && <NavBadge count={todoCount} label="未处理待办" />}
              </Link>
              <Link to="/notifications" className="relative hover:text-white">
                通知
                {unreadCount > 0 && <NavBadge count={unreadCount} label="未读通知" />}
              </Link>
              <Link to="/agents" className="hover:text-white" data-testid="nav-agents">
                Agents
              </Link>
              {!isAdmin && projectKey && (
                <span className="font-mono text-slate-500">{projectKey}</span>
              )}
            </nav>
          </div>
          <div className="flex items-center gap-3 text-sm text-slate-400">
            {role && (
              <span
                className={`badge ${isAdmin ? "bg-accent-muted text-accent" : "bg-surface text-slate-400"}`}
              >
                {role}
              </span>
            )}
            <div className="relative">
              <button
                type="button"
                className="rounded border border-surface-border bg-surface px-2 py-1 font-mono text-xs text-slate-200 hover:bg-surface-border"
                onClick={() => setShowIdentityMenu((v) => !v)}
                title="切换当前 Persona"
              >
                {agentName ?? "Agent"}
              </button>
              {showIdentityMenu && (
                <div className="absolute right-0 z-50 mt-2 w-80 rounded-md border border-surface-border bg-surface-raised p-2 shadow-xl">
                  <div className="px-2 pb-2 text-xs font-medium text-slate-500">切换 Persona</div>
                  <div className="max-h-72 space-y-1 overflow-y-auto">
                    {identities.length === 0 ? (
                      <p className="px-2 py-3 text-sm text-slate-500">尚未保存身份</p>
                    ) : (
                      identities.map((identity) => (
                        <IdentityMenuItem
                          key={identity.id}
                          identity={identity}
                          active={identity.id === activeIdentityId}
                          onSelect={() => {
                            switchIdentity(identity.id);
                            setShowIdentityMenu(false);
                          }}
                        />
                      ))
                    )}
                  </div>
                  <div className="mt-2 flex items-center justify-between border-t border-surface-border pt-2">
                    <Link
                      to="/settings"
                      className="rounded px-2 py-1 text-xs text-accent hover:bg-surface"
                      onClick={() => setShowIdentityMenu(false)}
                    >
                      添加/管理身份
                    </Link>
                    <Link
                      to="/agents"
                      className="rounded px-2 py-1 text-xs text-slate-400 hover:bg-surface hover:text-white"
                      onClick={() => setShowIdentityMenu(false)}
                    >
                      Agents
                    </Link>
                  </div>
                </div>
              )}
            </div>
            {isAdmin && (
              <button
                type="button"
                className="btn-secondary py-1 text-xs"
                onClick={() => setShowCreateProject(true)}
              >
                创建项目
              </button>
            )}
            <Link to="/settings" className="hover:text-white">
              设置
            </Link>
            <button type="button" onClick={clearToken} className="hover:text-white">
              退出
            </button>
          </div>
        </div>
      </header>

      {showCreateProject && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 px-4">
          <div className="card w-full max-w-md">
            <h2 className="mb-4 text-lg font-semibold text-white">创建项目</h2>
            <CreateProjectForm
              onCancel={() => setShowCreateProject(false)}
              onCreated={() => setShowCreateProject(false)}
            />
          </div>
        </div>
      )}

      <main className="mx-auto max-w-6xl px-4 py-6">
        <Outlet />
      </main>
    </div>
  );
}

function personaLabel(identity: AuthIdentity): string {
  const lowerName = identity.agentName.toLowerCase();
  if (lowerName.endsWith("-host")) return "host";
  if (lowerName.endsWith("-participant")) return "participant";
  if (lowerName.endsWith("-reviewer")) return "reviewer";
  return identity.role;
}

function IdentityMenuItem({
  identity,
  active,
  onSelect,
}: {
  identity: AuthIdentity;
  active: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      className={`w-full rounded px-2 py-2 text-left text-sm ${
        active ? "bg-accent-muted text-white" : "text-slate-300 hover:bg-surface"
      }`}
    >
      <div className="flex items-center justify-between gap-3">
        <span className="truncate font-mono text-xs">{identity.agentName}</span>
        <span className={`badge ${active ? "bg-accent text-white" : "bg-surface text-slate-400"}`}>
          {personaLabel(identity)}
        </span>
      </div>
      <div className="mt-1 flex items-center gap-2 text-xs text-slate-500">
        {identity.projectKey && <span className="font-mono">{identity.projectKey}</span>}
        {active && <span>当前</span>}
      </div>
    </button>
  );
}

function NavBadge({ count, label }: { count: number; label: string }) {
  return (
    <span
      className="absolute -right-3 -top-2 flex h-4 min-w-4 items-center justify-center rounded-full bg-accent px-1 text-[10px] font-bold text-white"
      title={`${count} 条${label}`}
      aria-label={`${count} 条${label}`}
    >
      {count > 99 ? "99+" : count}
    </span>
  );
}
