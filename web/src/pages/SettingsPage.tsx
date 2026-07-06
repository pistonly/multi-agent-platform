import { useState } from "react";
import { Link } from "react-router-dom";
import { CreateProjectForm } from "../components/CreateProjectForm";
import { useAuth } from "../context/AuthContext";
import type { AuthIdentity } from "../context/authIdentities";

export function SettingsPage() {
  const {
    token,
    agent,
    agentName,
    role,
    projectKey,
    isReady,
    identities,
    activeIdentityId,
    setToken,
    switchIdentity,
    removeIdentity,
    clearToken,
  } = useAuth();
  const [input, setInput] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [showCreateProject, setShowCreateProject] = useState(false);

  if (!isReady) {
    return <div className="flex min-h-screen items-center justify-center text-slate-400">加载中…</div>;
  }

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await setToken(input.trim());
      setInput("");
    } catch {
      setError("Token 无效，请确认后重试");
    } finally {
      setLoading(false);
    }
  }

  if (token && agent) {
    return (
      <div className="mx-auto max-w-3xl px-4 py-10">
        <div className="card space-y-4">
          <div className="flex items-center justify-between">
            <h1 className="text-xl font-bold text-white">设置</h1>
            <Link to="/" className="text-sm text-accent hover:underline">
              返回
            </Link>
          </div>

          <dl className="space-y-2 text-sm">
            <div className="flex justify-between gap-4">
              <dt className="text-slate-500">当前 Agent</dt>
              <dd className="text-right text-white">{agentName}</dd>
            </div>
            <div className="flex justify-between gap-4">
              <dt className="text-slate-500">权限角色</dt>
              <dd>
                <span
                  className={`badge ${role === "admin" ? "bg-accent-muted text-accent" : "bg-surface text-slate-300"}`}
                >
                  {role}
                </span>
              </dd>
            </div>
            {projectKey && (
              <div className="flex justify-between gap-4">
                <dt className="text-slate-500">绑定项目</dt>
                <dd className="font-mono text-white">{projectKey}</dd>
              </div>
            )}
          </dl>

          <div className="border-t border-surface-border pt-4">
            <h2 className="mb-3 text-sm font-semibold text-white">已保存身份</h2>
            <div className="space-y-2">
              {identities.map((identity) => (
                <IdentityRow
                  key={identity.id}
                  identity={identity}
                  active={identity.id === activeIdentityId}
                  onSwitch={() => switchIdentity(identity.id)}
                  onRemove={() => removeIdentity(identity.id)}
                  disableRemove={identities.length === 1}
                />
              ))}
              {identities.length === 0 && (
                <p className="rounded border border-surface-border bg-surface/40 px-3 py-4 text-sm text-slate-500">
                  尚未保存身份。
                </p>
              )}
            </div>
          </div>

          <div className="border-t border-surface-border pt-4">
            <h2 className="mb-3 text-sm font-semibold text-white">添加 Persona Token</h2>
            <TokenForm
              input={input}
              setInput={setInput}
              error={error}
              loading={loading}
              buttonText={loading ? "验证中…" : "验证并添加"}
              onSubmit={handleSave}
            />
          </div>

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
              清除所有身份
            </button>
          </div>
        </div>
      </div>
    );
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
        <TokenForm
          input={input}
          setInput={setInput}
          error={error}
          loading={loading}
          buttonText={loading ? "验证中…" : "保存并进入"}
          onSubmit={handleSave}
        />
      </div>
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

function IdentityRow({
  identity,
  active,
  onSwitch,
  onRemove,
  disableRemove,
}: {
  identity: AuthIdentity;
  active: boolean;
  onSwitch: () => void;
  onRemove: () => void;
  disableRemove: boolean;
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded border border-surface-border bg-surface/40 px-3 py-3">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <span className="truncate font-mono text-sm text-white">{identity.agentName}</span>
          <span className="badge bg-surface text-slate-300">{personaLabel(identity)}</span>
          {active && <span className="badge bg-emerald-900/40 text-emerald-200">当前</span>}
        </div>
        <div className="mt-1 flex flex-wrap gap-2 text-xs text-slate-500">
          {identity.projectKey && <span className="font-mono">{identity.projectKey}</span>}
          <span className="font-mono">{identity.agentId}</span>
        </div>
      </div>
      <div className="flex items-center gap-2">
        {!active && (
          <button type="button" className="btn-secondary py-1 text-xs" onClick={onSwitch}>
            切换
          </button>
        )}
        <button
          type="button"
          className="btn-secondary py-1 text-xs"
          onClick={onRemove}
          disabled={disableRemove}
          title={disableRemove ? "至少保留一个身份；可使用清除所有身份退出" : "删除此身份"}
        >
          删除
        </button>
      </div>
    </div>
  );
}

function TokenForm({
  input,
  setInput,
  error,
  loading,
  buttonText,
  onSubmit,
}: {
  input: string;
  setInput: (value: string) => void;
  error: string | null;
  loading: boolean;
  buttonText: string;
  onSubmit: (event: React.FormEvent) => void;
}) {
  return (
    <form onSubmit={onSubmit} className="space-y-4">
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
        {buttonText}
      </button>
    </form>
  );
}
