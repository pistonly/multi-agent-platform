import { useQuery } from "@tanstack/react-query";
import { fetchAgents, type FetchAgentsOptions } from "../api/client";
import type { Agent } from "../api/types";

const STALE_TIME_MS = 5 * 60 * 1000;

export interface UseAgentsResult {
  agents: Agent[];
  byId: Record<string, Agent>;
  isLoading: boolean;
  error: unknown;
  refetch: () => void;
}

/**
 * Cache the list of agents visible to the current viewer and provide a
 * by-id lookup map so comment/experiment renders can resolve a display
 * name without an extra request.
 */
export function useAgents(opts: FetchAgentsOptions = {}): UseAgentsResult {
  const query = useQuery({
    queryKey: ["agents", opts],
    queryFn: () => fetchAgents(opts),
    staleTime: STALE_TIME_MS,
    refetchOnWindowFocus: false,
  });

  const agents = query.data ?? [];
  const byId: Record<string, Agent> = {};
  for (const agent of agents) {
    byId[agent.id] = agent;
  }

  return {
    agents,
    byId,
    isLoading: query.isLoading,
    error: query.error,
    refetch: () => {
      void query.refetch();
    },
  };
}
