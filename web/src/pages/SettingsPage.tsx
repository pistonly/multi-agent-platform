import { useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { CreateProjectForm } from "../components/CreateProjectForm";

export function SettingsPage() {
  const { token, agent, agentName, role, projectKey, isReady, setToken, clearToken } = useAuth();
  const [input, setInput] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [showCreateProject, setShowCreateProject] = useState(false);

  if (!isReady) {
    return <div className="flex min-h-screen items-center justify-center text-slate-400">加载中…</div>;
  }

  if (token && agent) {
    return (
      <div className="mx-auto max-w-lg px-4 py-10">
        <div className="card space-y-4">
          <div className="flex items-center justify-between">
            <h1 className="text-xl font-bold text-white">设置</h1>
            <Link to="/" className="text-sm text-accent hover:underline">
              返回
            </Link>
          </div>

          <dl className="space-y-2 text-sm">
            <div className="flex justify-between">
              <dt className="text-slate-500">Agent</dt>
              <dd className="text-white">{agentName}</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-slate-500">角色</dt>
              <dd>
                <span
                  className={`badge ${role === "admin" ? "bg-accent-muted text-accent" : "bg-surface text-slate-300"}`}
                >
                  {role}
                </span>
              </dd>
            </div>
            {projectKey && (
              <div className="flex justify-between">
                <dt className="text-slate-500">绑定项目</dt>
                <dd className="font-mono text-white">{projectKey}</dd>
              </div>
            )}
          </dl>

          {role === "admin" && (
            <div className="border-t border-surface-border pt-4">
              {!showCreateProject ? (
                <button
                  type="button"
                  className="btn-primary"
                  onClick={() => setShowCreateProject(true)}
                >
                  创建项目
                </button>
              ) : (
                <CreateProjectForm
                  onCancel={() => setShowCreateProject(false)}
                  onCreated={() => setShowCreateProject(false)}
                />
              )}
            </div>
          )}

          <div className="border-t border-surface-border pt-4">
            <button type="button" className="btn-secondary" onClick={clearToken}>
              退出登录
            </button>
          </div>
        </div>
      </div>
    );
  }

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await setToken(input.trim());
    } catch {
      setError("Token 无效，请确认后重试");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <div className="card w-full max-w-md">
        <h1 className="mb-2 text-xl font-bold text-white">连接 MAP</h1>
        <p className="mb-6 text-sm text-slate-400">
          输入 Agent API Token 以访问看板。首次部署可匿名注册 Admin（
          <code className="rounded bg-surface px-1">POST /api/v1/agents?name=...&amp;role=admin</code>
          ）；其余 Agent 须由 Admin 注册后获取 Token。
        </p>
        <form onSubmit={handleSave} className="space-y-4">
          <div>
            <label htmlFor="token" className="mb-1 block text-sm text-slate-300">
              API Token
            </label>
            <input
              id="token"
              type="password"
              className="w-full rounded border border-surface-border bg-surface px-3 py-2 text-sm text-white"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Bearer token"
              required
            />
          </div>
          {error && <p className="text-sm text-red-400">{error}</p>}
          <button type="submit" className="btn-primary w-full" disabled={loading}>
            {loading ? "验证中…" : "保存并进入"}
          </button>
        </form>
      </div>
    </div>
  );
}
