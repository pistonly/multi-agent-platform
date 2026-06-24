import { useState } from "react";
import { Link, Outlet, Navigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { CreateProjectForm } from "./CreateProjectForm";

export function Layout() {
  const { token, agentName, role, projectKey, isAdmin, isReady, clearToken } = useAuth();
  const [showCreateProject, setShowCreateProject] = useState(false);

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
            <span>{agentName ?? "Agent"}</span>
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
