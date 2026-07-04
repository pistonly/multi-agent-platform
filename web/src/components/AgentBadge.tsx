import type { Agent, AgentRole } from "../api/types";
import { useAgents } from "../hooks/useAgents";

interface AgentBadgeProps {
  agentId: string;
  fallbackName?: string | null;
  fallbackRole?: AgentRole | null;
  className?: string;
  showRole?: boolean;
  compact?: boolean;
}

const ROLE_STYLES: Record<AgentRole, string> = {
  admin: "bg-accent-muted text-accent",
  agent: "bg-surface text-slate-300",
};

function initials(name: string): string {
  const cleaned = name.trim();
  if (!cleaned) return "?";
  const first = cleaned[0];
  if (!first) return "?";
  return first.toUpperCase();
}

function describeAgent(agent: Agent): string {
  const parts = [agent.name];
  if (agent.role) parts.push(`role=${agent.role}`);
  if (agent.project_key) parts.push(`project=${agent.project_key}`);
  parts.push(`id=${agent.id}`);
  parts.push(`created_at=${agent.created_at}`);
  return parts.join("\n");
}

/**
 * Renders a small badge for an agent (id + name + role). Falls back to a
 * UUID prefix when the agent is not yet in the local cache so the UI never
 * becomes empty mid-flight.
 */
export function AgentBadge({
  agentId,
  fallbackName,
  fallbackRole,
  className,
  showRole = true,
  compact = false,
}: AgentBadgeProps) {
  const { byId } = useAgents();
  const agent = byId[agentId];

  const name = agent?.name ?? fallbackName ?? `${agentId.slice(0, 8)}…`;
  const role: AgentRole | undefined = agent?.role ?? fallbackRole ?? undefined;
  const roleClass = role ? ROLE_STYLES[role] : ROLE_STYLES.agent;
  const tooltip = agent ? describeAgent(agent) : `未知 Agent · ${agentId}`;

  return (
    <span
      className={`inline-flex max-w-[200px] items-center gap-1.5 truncate rounded-full border border-surface-border bg-surface/60 px-2 py-0.5 text-xs ${
        className ?? ""
      }`}
      title={tooltip}
      data-testid={`agent-badge-${agentId}`}
    >
      <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-slate-700 text-[10px] font-semibold text-white">
        {initials(name)}
      </span>
      <span className="truncate text-slate-100">{name}</span>
      {showRole && role && !compact && (
        <span className={`badge shrink-0 text-[10px] ${roleClass}`}>{role}</span>
      )}
    </span>
  );
}
