import { Link, Outlet, Navigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";

export function Layout() {
  const { token, agentName, isReady, clearToken } = useAuth();

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
                看板
              </Link>
            </nav>
          </div>
          <div className="flex items-center gap-3 text-sm text-slate-400">
            <span>{agentName ?? "Agent"}</span>
            <Link to="/settings" className="hover:text-white">
              设置
            </Link>
            <button type="button" onClick={clearToken} className="hover:text-white">
              退出
            </button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-4 py-6">
        <Outlet />
      </main>
    </div>
  );
}
