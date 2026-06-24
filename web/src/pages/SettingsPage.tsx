import { useState } from "react";
import { Navigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";

export function SettingsPage() {
  const { token, agentName, setToken, isReady } = useAuth();
  const [input, setInput] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  if (!isReady) {
    return <div className="flex min-h-screen items-center justify-center text-slate-400">加载中…</div>;
  }

  if (token) {
    return <Navigate to="/" replace />;
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
          输入 Agent API Token 以访问看板。可通过{" "}
          <code className="rounded bg-surface px-1">POST /api/v1/agents?name=...</code> 注册获取。
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
        {agentName && <p className="mt-4 text-sm text-emerald-400">已连接：{agentName}</p>}
      </div>
    </div>
  );
}
