import { describe, expect, it } from "vitest";
import type { Agent } from "../api/types";
import {
  ACTIVE_IDENTITY_KEY,
  IDENTITIES_KEY,
  LEGACY_TOKEN_KEY,
  agentFromIdentity,
  chooseActiveIdentity,
  identityFromAgent,
  mergeIdentity,
  persistIdentities,
  readStoredIdentities,
  removeIdentityById,
  touchIdentity,
} from "./authIdentities";

function agent(overrides: Partial<Agent> = {}): Agent {
  return {
    id: "agent-1",
    name: "demo-host",
    role: "agent",
    project_id: "project-1",
    project_key: "demo",
    created_at: "2026-07-05T00:00:00Z",
    ...overrides,
  };
}

class MemoryStorage {
  private values = new Map<string, string>();

  getItem(key: string): string | null {
    return this.values.get(key) ?? null;
  }

  setItem(key: string, value: string) {
    this.values.set(key, value);
  }

  removeItem(key: string) {
    this.values.delete(key);
  }
}

describe("auth identities", () => {
  it("builds an identity from /agents/me and can reconstruct the agent", () => {
    const identity = identityFromAgent(agent(), "token-1", new Date("2026-07-05T01:00:00Z"));

    expect(identity).toMatchObject({
      id: "agent:agent-1",
      token: "token-1",
      agentId: "agent-1",
      agentName: "demo-host",
      projectKey: "demo",
    });
    expect(agentFromIdentity(identity)).toEqual(agent());
  });

  it("persists identities and removes the legacy single-token key", () => {
    const storage = new MemoryStorage();
    storage.setItem(LEGACY_TOKEN_KEY, "old-token");
    const identity = identityFromAgent(agent(), "token-1");

    persistIdentities(storage, [identity], identity.id);

    expect(storage.getItem(LEGACY_TOKEN_KEY)).toBeNull();
    expect(storage.getItem(ACTIVE_IDENTITY_KEY)).toBe(identity.id);
    expect(readStoredIdentities(storage)).toEqual([identity]);
  });

  it("drops malformed stored rows", () => {
    const storage = new MemoryStorage();
    storage.setItem(
      IDENTITIES_KEY,
      JSON.stringify([{ id: "bad" }, identityFromAgent(agent(), "token-1")])
    );

    expect(readStoredIdentities(storage)).toHaveLength(1);
  });

  it("updates an existing identity without duplicating it", () => {
    const first = identityFromAgent(agent(), "token-1", new Date("2026-07-05T01:00:00Z"));
    const second = identityFromAgent(
      agent({ name: "demo-host-renamed" }),
      "token-2",
      new Date("2026-07-05T02:00:00Z")
    );

    expect(mergeIdentity([first], second)).toEqual([
      {
        ...second,
        savedAt: first.savedAt,
      },
    ]);
  });

  it("chooses the preferred identity or the most recently used fallback", () => {
    const host = identityFromAgent(
      agent({ id: "host", name: "demo-host" }),
      "host-token",
      new Date("2026-07-05T01:00:00Z")
    );
    const reviewer = identityFromAgent(
      agent({ id: "reviewer", name: "demo-reviewer" }),
      "reviewer-token",
      new Date("2026-07-05T02:00:00Z")
    );
    const touched = touchIdentity([host, reviewer], reviewer.id, new Date("2026-07-05T03:00:00Z"));

    expect(chooseActiveIdentity(touched, host.id)?.id).toBe(host.id);
    expect(chooseActiveIdentity(touched, "missing")?.id).toBe(reviewer.id);
  });

  it("removes identities by id", () => {
    const host = identityFromAgent(agent({ id: "host" }), "host-token");
    const reviewer = identityFromAgent(agent({ id: "reviewer" }), "reviewer-token");

    expect(removeIdentityById([host, reviewer], host.id)).toEqual([reviewer]);
  });
});
