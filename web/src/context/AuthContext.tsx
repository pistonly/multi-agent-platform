import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { getMe, setAuthToken } from "../api/client";

const TOKEN_KEY = "map_api_token";

interface AuthContextValue {
  token: string | null;
  agentName: string | null;
  setToken: (token: string) => Promise<void>;
  clearToken: () => void;
  isReady: boolean;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setTokenState] = useState<string | null>(() => localStorage.getItem(TOKEN_KEY));
  const [agentName, setAgentName] = useState<string | null>(null);
  const [isReady, setIsReady] = useState(false);

  useEffect(() => {
    let cancelled = false;
    async function verify() {
      if (!token) {
        setAuthToken(null);
        setAgentName(null);
        setIsReady(true);
        return;
      }
      setAuthToken(token);
      try {
        const me = await getMe();
        if (!cancelled) {
          setAgentName(me.name);
          setIsReady(true);
        }
      } catch {
        if (!cancelled) {
          localStorage.removeItem(TOKEN_KEY);
          setTokenState(null);
          setAuthToken(null);
          setAgentName(null);
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
      agentName,
      isReady,
      setToken: async (newToken: string) => {
        localStorage.setItem(TOKEN_KEY, newToken);
        setAuthToken(newToken);
        const me = await getMe();
        setAgentName(me.name);
        setTokenState(newToken);
      },
      clearToken: () => {
        localStorage.removeItem(TOKEN_KEY);
        setAuthToken(null);
        setTokenState(null);
        setAgentName(null);
      },
    }),
    [token, agentName, isReady]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
