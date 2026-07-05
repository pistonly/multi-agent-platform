import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { getMe, setAuthToken } from "../api/client";
import type { Agent, AgentRole } from "../api/types";
import {
  ACTIVE_IDENTITY_KEY,
  LEGACY_TOKEN_KEY,
  type AuthIdentity,
  agentFromIdentity,
  chooseActiveIdentity,
  identityFromAgent,
  mergeIdentity,
  persistIdentities,
  readStoredIdentities,
  removeIdentityById,
  touchIdentity,
} from "./authIdentities";

interface AuthContextValue {
  token: string | null;
  agent: Agent | null;
  identities: AuthIdentity[];
  activeIdentityId: string | null;
  activeIdentity: AuthIdentity | null;
  agentName: string | null;
  role: AgentRole | null;
  projectId: string | null;
  projectKey: string | null;
  isAdmin: boolean;
  setToken: (token: string) => Promise<void>;
  switchIdentity: (identityId: string) => void;
  removeIdentity: (identityId: string) => void;
  clearToken: () => void;
  isReady: boolean;
}

const AuthContext = createContext<AuthContextValue | null>(null);

function initialAuthState() {
  const identities = readStoredIdentities(localStorage);
  const activeIdentity = chooseActiveIdentity(
    identities,
    localStorage.getItem(ACTIVE_IDENTITY_KEY)
  );
  const legacyToken = localStorage.getItem(LEGACY_TOKEN_KEY);
  return {
    identities,
    activeIdentityId: activeIdentity?.id ?? null,
    token: activeIdentity?.token ?? legacyToken,
    agent: activeIdentity ? agentFromIdentity(activeIdentity) : null,
  };
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const initial = useMemo(() => initialAuthState(), []);
  const [identities, setIdentities] = useState<AuthIdentity[]>(initial.identities);
  const [activeIdentityId, setActiveIdentityId] = useState<string | null>(
    initial.activeIdentityId
  );
  const [token, setTokenState] = useState<string | null>(initial.token);
  const [agent, setAgent] = useState<Agent | null>(initial.agent);
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
          const identity = identityFromAgent(me, token);
          setIdentities((prev) => {
            const next = mergeIdentity(prev, identity);
            persistIdentities(localStorage, next, identity.id);
            return next;
          });
          setActiveIdentityId(identity.id);
          setAgent(me);
          setIsReady(true);
        }
      } catch {
        if (!cancelled) {
          const fallback = chooseActiveIdentity(
            identities.filter((identity) => identity.token !== token),
            null
          );
          const nextIdentities = identities.filter((identity) => identity.token !== token);
          persistIdentities(localStorage, nextIdentities, fallback?.id ?? null);
          setAuthToken(null);
          setIdentities(nextIdentities);
          setActiveIdentityId(fallback?.id ?? null);
          setTokenState(fallback?.token ?? null);
          setAgent(fallback ? agentFromIdentity(fallback) : null);
          if (fallback) {
            setAuthToken(fallback.token);
            queryClient.clear();
          } else {
            setIsReady(true);
          }
        }
      }
    }
    verify();
    return () => {
      cancelled = true;
    };
  }, [token, queryClient]);

  const activeIdentity =
    identities.find((identity) => identity.id === activeIdentityId) ?? null;

  const value = useMemo<AuthContextValue>(
    () => ({
      token,
      agent,
      identities,
      activeIdentityId,
      activeIdentity,
      agentName: agent?.name ?? null,
      role: agent?.role ?? null,
      projectId: agent?.project_id ?? null,
      projectKey: agent?.project_key ?? null,
      isAdmin: agent?.role === "admin",
      isReady,
      setToken: async (newToken: string) => {
        const trimmed = newToken.trim();
        if (!trimmed) {
          throw new Error("Token is required");
        }
        const previousToken = token;
        setAuthToken(trimmed);
        try {
          const me = await getMe();
          const identity = identityFromAgent(me, trimmed);
          const next = mergeIdentity(identities, identity);
          persistIdentities(localStorage, next, identity.id);
          queryClient.clear();
          setIdentities(next);
          setActiveIdentityId(identity.id);
          setAgent(me);
          setTokenState(trimmed);
          setIsReady(true);
        } catch (error) {
          setAuthToken(previousToken);
          throw error;
        }
      },
      switchIdentity: (identityId: string) => {
        const identity = identities.find((item) => item.id === identityId);
        if (!identity) return;
        const next = touchIdentity(identities, identityId);
        persistIdentities(localStorage, next, identity.id);
        queryClient.clear();
        setIdentities(next);
        setActiveIdentityId(identity.id);
        setTokenState(identity.token);
        setAgent(agentFromIdentity(identity));
        setAuthToken(identity.token);
        setIsReady(true);
      },
      removeIdentity: (identityId: string) => {
        const next = removeIdentityById(identities, identityId);
        const fallback =
          identityId === activeIdentityId ? chooseActiveIdentity(next, null) : activeIdentity;
        persistIdentities(localStorage, next, fallback?.id ?? null);
        setIdentities(next);
        setActiveIdentityId(fallback?.id ?? null);
        if (identityId === activeIdentityId) {
          queryClient.clear();
          setTokenState(fallback?.token ?? null);
          setAgent(fallback ? agentFromIdentity(fallback) : null);
          setAuthToken(fallback?.token ?? null);
        }
      },
      clearToken: () => {
        persistIdentities(localStorage, [], null);
        queryClient.clear();
        setAuthToken(null);
        setTokenState(null);
        setAgent(null);
        setIdentities([]);
        setActiveIdentityId(null);
        setIsReady(true);
      },
    }),
    [
      token,
      agent,
      identities,
      activeIdentityId,
      activeIdentity,
      isReady,
      queryClient,
    ]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
