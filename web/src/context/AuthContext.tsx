import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { getMe, setAuthToken } from "../api/client";
import type { Agent, AgentRole } from "../api/types";

const TOKEN_KEY = "map_api_token";

interface AuthContextValue {
  token: string | null;
  agent: Agent | null;
  agentName: string | null;
  role: AgentRole | null;
  projectId: string | null;
  projectKey: string | null;
  isAdmin: boolean;
  setToken: (token: string) => Promise<void>;
  clearToken: () => void;
  isReady: boolean;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setTokenState] = useState<string | null>(() => localStorage.getItem(TOKEN_KEY));
  const [agent, setAgent] = useState<Agent | null>(null);
  const [isReady, setIsReady] = useState(false);

  useEffect(() => {
    let cancelled = false;
    async function verify() {
      if (!token) {
        setAuthToken(null);
        setAgent(null);
        setIsReady(true);
        return;
      }
      setAuthToken(token);
      try {
        const me = await getMe();
        if (!cancelled) {
          setAgent(me);
          setIsReady(true);
        }
      } catch {
        if (!cancelled) {
          localStorage.removeItem(TOKEN_KEY);
          setTokenState(null);
          setAuthToken(null);
          setAgent(null);
          setIsReady(true);
        }
      }
    }
    verify();
    return () => {
      cancelled = true;
    };
  }, [token]);

  const value = useMemo<AuthContextValue>(
    () => ({
      token,
      agent,
      agentName: agent?.name ?? null,
      role: agent?.role ?? null,
      projectId: agent?.project_id ?? null,
      projectKey: agent?.project_key ?? null,
      isAdmin: agent?.role === "admin",
      isReady,
      setToken: async (newToken: string) => {
        localStorage.setItem(TOKEN_KEY, newToken);
        setAuthToken(newToken);
        const me = await getMe();
        setAgent(me);
        setTokenState(newToken);
      },
      clearToken: () => {
        localStorage.removeItem(TOKEN_KEY);
        setAuthToken(null);
        setTokenState(null);
        setAgent(null);
      },
    }),
    [token, agent, isReady]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
