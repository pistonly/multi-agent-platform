import { Link } from "react-router-dom";
import type { TopicDecision } from "../../api/types";
import { AgentBadge } from "../../components/AgentBadge";
import { MarkdownBody } from "../../components/MarkdownBody";

// T37: extracted verbatim from pages/TopicPage.tsx. The structured decision
// panel (with subsections and action items) is self-contained — only the
// TopicPage host needs it, and DecisionSubsection is private to this module.
export function TopicDecisionPanel({ decision }: { decision: TopicDecision | null }) {
  if (!decision) {
    return (
      <section className="card">
        <h2 className="mb-2 text-lg font-semibold text-white">结论</h2>
        <p className="text-sm text-slate-500">暂无结构化结论</p>
      </section>
    );
  }

  return (
    <section className="card">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-lg font-semibold text-white">结论</h2>
        <span className="flex items-center gap-2 text-xs text-slate-500">
          <AgentBadge
            agentId={decision.author_agent_id}
            fallbackName={decision.author_name}
            compact
          />
          <span>· {new Date(decision.updated_at).toLocaleString()}</span>
        </span>
      </div>
      {decision.decision ? <MarkdownBody content={decision.decision} /> : null}
      {decision.no_decision_reason ? (
        <div className="rounded border border-amber-800 bg-amber-950/30 p-3 text-sm text-amber-100">
          <MarkdownBody content={decision.no_decision_reason} />
        </div>
      ) : null}
      {decision.rationale ? (
        <DecisionSubsection title="依据" content={decision.rationale} />
      ) : null}
      {decision.rejected_options ? (
        <DecisionSubsection title="未采纳选项" content={decision.rejected_options} />
      ) : null}
      {decision.open_questions ? (
        <DecisionSubsection title="开放问题" content={decision.open_questions} />
      ) : null}
      {(decision.action_items ?? []).length > 0 && (
        <div className="mt-4">
          <h3 className="mb-2 text-sm font-semibold text-slate-300">行动项</h3>
          <ul className="space-y-2 text-sm">
            {(decision.action_items ?? []).map((item) => (
              <li key={item.id} className="rounded border border-surface-border bg-surface/60 px-3 py-2">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="font-medium text-white">{item.title}</span>
                  <span className="badge bg-slate-700 text-slate-200">{item.status}</span>
                </div>
                <div className="mt-1 flex flex-wrap items-center gap-3 text-xs text-slate-500">
                  {item.owner_agent_id ? (
                    <AgentBadge
                      agentId={item.owner_agent_id}
                      fallbackName={item.owner_name}
                      compact
                    />
                  ) : (
                    <span>未分配</span>
                  )}
                  {item.due_at && <span>截止 {new Date(item.due_at).toLocaleString()}</span>}
                  {item.linked_experiment_id && (
                    <Link to={`/experiments/${item.linked_experiment_id}`} className="text-accent hover:underline">
                      关联实验
                    </Link>
                  )}
                </div>
                {item.description && <p className="mt-1 text-xs text-slate-400">{item.description}</p>}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

function DecisionSubsection({ title, content }: { title: string; content: string }) {
  return (
    <div className="mt-4 border-t border-surface-border pt-3">
      <h3 className="mb-1 text-sm font-semibold text-slate-300">{title}</h3>
      <MarkdownBody content={content} />
    </div>
  );
}
