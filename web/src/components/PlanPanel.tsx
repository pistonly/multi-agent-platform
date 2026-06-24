import ReactMarkdown from "react-markdown";
import type { PlanVersion } from "../api/types";

interface PlanPanelProps {
  plan: PlanVersion | null;
  versions: PlanVersion[];
  version: number;
  onSelectVersion: (v: number) => void;
}

export function PlanPanel({ plan, versions, version, onSelectVersion }: PlanPanelProps) {
  return (
    <div className="card h-full">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-base font-semibold text-white">实验计划 v{version}</h2>
        {versions.length > 1 && (
          <select
            className="rounded border border-surface-border bg-surface px-2 py-1 text-sm text-slate-200"
            value={version}
            onChange={(e) => onSelectVersion(Number(e.target.value))}
          >
            {versions.map((p) => (
              <option key={p.id} value={p.version}>
                v{p.version}
                {p.change_note ? ` — ${p.change_note}` : ""}
              </option>
            ))}
          </select>
        )}
      </div>
      {plan ? (
        <div className="markdown-body max-h-[480px] overflow-y-auto">
          <ReactMarkdown>{plan.content_md}</ReactMarkdown>
        </div>
      ) : (
        <p className="text-sm text-slate-500">暂无计划</p>
      )}
    </div>
  );
}
