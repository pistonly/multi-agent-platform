import type { ExperimentPhase } from "../api/types";

const PHASES: ExperimentPhase[] = ["draft", "review", "approved", "running", "done"];

const LABELS: Record<ExperimentPhase, string> = {
  draft: "草稿",
  review: "评审",
  approved: "已批准",
  running: "执行中",
  done: "完成",
  cancelled: "已取消",
};

const COLORS: Record<ExperimentPhase, string> = {
  draft: "bg-slate-600 text-slate-100",
  review: "bg-amber-600/80 text-amber-50",
  approved: "bg-emerald-700/80 text-emerald-50",
  running: "bg-blue-600/80 text-blue-50",
  done: "bg-slate-500 text-slate-100",
  cancelled: "bg-red-900/60 text-red-100",
};

export function phaseLabel(phase: ExperimentPhase): string {
  return LABELS[phase] ?? phase;
}

export function PhaseBadge({ phase }: { phase: ExperimentPhase }) {
  return <span className={`badge ${COLORS[phase]}`}>{phaseLabel(phase)}</span>;
}

export function PhaseStepper({ phase }: { phase: ExperimentPhase }) {
  if (phase === "cancelled") {
    return (
      <div className="flex items-center gap-2 text-sm text-red-300">
        <PhaseBadge phase="cancelled" />
        <span>实验已取消</span>
      </div>
    );
  }

  const currentIdx = PHASES.indexOf(phase);

  return (
    <ol className="flex flex-wrap items-center gap-1 text-xs sm:text-sm">
      {PHASES.map((step, idx) => {
        const active = idx === currentIdx;
        const done = idx < currentIdx;
        return (
          <li key={step} className="flex items-center gap-1">
            {idx > 0 && <span className="text-surface-border px-1">→</span>}
            <span
              className={`rounded-full px-2 py-1 ${
                active
                  ? "bg-accent font-semibold text-white"
                  : done
                    ? "bg-emerald-900/50 text-emerald-200"
                    : "bg-surface-border text-slate-400"
              }`}
            >
              {phaseLabel(step)}
            </span>
          </li>
        );
      })}
    </ol>
  );
}
