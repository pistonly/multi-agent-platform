import { diffLines } from "diff";

interface PlanDiffViewProps {
  oldText: string;
  newText: string;
  oldLabel: string;
  newLabel: string;
}

export function PlanDiffView({ oldText, newText, oldLabel, newLabel }: PlanDiffViewProps) {
  const parts = diffLines(oldText, newText);

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-3 text-xs text-slate-500">
        <span>对比：{oldLabel}</span>
        <span>→</span>
        <span>{newLabel}</span>
      </div>
      <pre className="max-h-[480px] overflow-auto rounded border border-surface-border bg-surface p-3 font-mono text-xs leading-relaxed">
        {parts.map((part, i) => {
          const cls = part.added
            ? "bg-emerald-900/40 text-emerald-100"
            : part.removed
              ? "bg-red-900/40 text-red-100"
              : "text-slate-300";
          const prefix = part.added ? "+ " : part.removed ? "- " : "  ";
          return (
            <span key={i} className={cls}>
              {part.value
                .split("\n")
                .filter((line, idx, arr) => line !== "" || idx < arr.length - 1)
                .map((line, lineIdx) => (
                  <span key={`${i}-${lineIdx}`}>
                    {prefix}
                    {line}
                    {"\n"}
                  </span>
                ))}
            </span>
          );
        })}
      </pre>
    </div>
  );
}
