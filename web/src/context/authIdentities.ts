import type { Agent, AgentRole } from "../api/types";

export const LEGACY_TOKEN_KEY = "map_api_token";
export const IDENTITIES_KEY = "map_auth_identities";
export const ACTIVE_IDENTITY_KEY = "map_active_identity_id";

export interface AuthIdentity {
  id: string;
  token: string;
  agentId: string;
  agentName: string;
  role: AgentRole;
  projectId: string | null;
  projectKey: string | null;
  createdAt: string | null;
  savedAt: string;
  lastUsedAt: string;
}

type StorageLike = Pick<Storage, "getItem" | "setItem" | "removeItem">;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function readString(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function normalizeIdentity(value: unknown): AuthIdentity | null {
  if (!isRecord(value)) return null;
  const id = readString(value.id);
  const token = readString(value.token);
  const agentId = readString(value.agentId);
  const agentName = readString(value.agentName);
  const role = value.role === "admin" || value.role === "agent" ? value.role : null;
  if (!id || !token || !agentId || !agentName || !role) return null;
  return {
    id,
    token,
    agentId,
    agentName,
    role,
    projectId: readString(value.projectId),
    projectKey: readString(value.projectKey),
    createdAt: readString(value.createdAt),
    savedAt: readString(value.savedAt) ?? new Date(0).toISOString(),
    lastUsedAt: readString(value.lastUsedAt) ?? new Date(0).toISOString(),
  };
}

export function identityIdForAgent(agentId: string): string {
  return `agent:${agentId}`;
}

export function identityFromAgent(agent: Agent, token: string, now = new Date()): AuthIdentity {
  const timestamp = now.toISOString();
  return {
    id: identityIdForAgent(agent.id),
    token,
    agentId: agent.id,
    agentName: agent.name,
    role: agent.role,
    projectId: agent.project_id,
    projectKey: agent.project_key,
    createdAt: agent.created_at,
    savedAt: timestamp,
    lastUsedAt: timestamp,
  };
}

export function agentFromIdentity(identity: AuthIdentity): Agent {
  return {
    id: identity.agentId,
    name: identity.agentName,
    role: identity.role,
    project_id: identity.projectId,
    project_key: identity.projectKey,
    created_at: identity.createdAt ?? identity.savedAt,
  };
}

export function readStoredIdentities(storage: StorageLike): AuthIdentity[] {
  const raw = storage.getItem(IDENTITIES_KEY);
  if (!raw) return [];
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    const identities: AuthIdentity[] = [];
    const seen = new Set<string>();
    for (const item of parsed) {
      const identity = normalizeIdentity(item);
      if (!identity || seen.has(identity.id)) continue;
      seen.add(identity.id);
      identities.push(identity);
    }
    return identities;
  } catch {
    return [];
  }
}

export function persistIdentities(
  storage: StorageLike,
  identities: AuthIdentity[],
  activeIdentityId: string | null
) {
  if (identities.length > 0) {
    storage.setItem(IDENTITIES_KEY, JSON.stringify(identities));
  } else {
    storage.removeItem(IDENTITIES_KEY);
  }
  if (activeIdentityId) {
    storage.setItem(ACTIVE_IDENTITY_KEY, activeIdentityId);
  } else {
    storage.removeItem(ACTIVE_IDENTITY_KEY);
  }
  storage.removeItem(LEGACY_TOKEN_KEY);
}

export function mergeIdentity(
  identities: AuthIdentity[],
  nextIdentity: AuthIdentity
): AuthIdentity[] {
  const existing = identities.find((identity) => identity.id === nextIdentity.id);
  if (!existing) return [...identities, nextIdentity];
  return identities.map((identity) =>
    identity.id === nextIdentity.id
      ? {
          ...identity,
          ...nextIdentity,
          savedAt: existing.savedAt,
          lastUsedAt: nextIdentity.lastUsedAt,
        }
      : identity
  );
}

export function touchIdentity(
  identities: AuthIdentity[],
  identityId: string,
  now = new Date()
): AuthIdentity[] {
  const timestamp = now.toISOString();
  return identities.map((identity) =>
    identity.id === identityId ? { ...identity, lastUsedAt: timestamp } : identity
  );
}

export function chooseActiveIdentity(
  identities: AuthIdentity[],
  preferredId: string | null
): AuthIdentity | null {
  if (identities.length === 0) return null;
  if (preferredId) {
    const preferred = identities.find((identity) => identity.id === preferredId);
    if (preferred) return preferred;
  }
  return [...identities].sort((a, b) => b.lastUsedAt.localeCompare(a.lastUsedAt))[0] ?? null;
}

export function removeIdentityById(
  identities: AuthIdentity[],
  identityId: string
): AuthIdentity[] {
  return identities.filter((identity) => identity.id !== identityId);
}
