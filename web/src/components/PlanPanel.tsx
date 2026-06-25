import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import ReactMarkdown from "react-markdown";
import { revisePlan } from "../api/client";
import type { PlanVersion } from "../api/types";

interface PlanPanelProps {
  experimentId: string;
  plan: PlanVersion | null;
  versions: PlanVersion[];
  version: number;
  onSelectVersion: (v: number) => void;
  onUpdated: () => void;
}

export function PlanPanel({
  experimentId,
  plan,
  versions,
  version,
  onSelectVersion,
  onUpdated,
}: PlanPanelProps) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [note, setNote] = useState("");

  const reviseMutation = useMutation({
    mutationFn: () =>
      revisePlan(experimentId, {
        content_md: draft.trim(),
        change_note: note.trim() || null,
      }),
    onSuccess: () => {
      setEditing(false);
      setDraft("");
      setNote("");
      onUpdated();
    },
  });

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

      {editing ? (
        <div className="mt-4 space-y-2 border-t border-surface-border pt-4">
          <textarea
            className="min-h-[120px] w-full rounded border border-surface-border bg-surface px-2 py-1 font-mono text-sm text-white"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="修订后的计划（Markdown）"
          />
          <input
            className="w-full rounded border border-surface-border bg-surface px-2 py-1 text-sm text-white"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="变更说明（可选）"
          />
          {reviseMutation.isError && <p className="text-sm text-red-400">提交失败，请检查权限</p>}
          <div className="flex items-center justify-between">
            <span className="text-xs text-slate-500">修订后将通知评审方重新确认</span>
            <div className="flex gap-2">
              <button
                type="button"
                className="btn-secondary"
                onClick={() => {
                  setEditing(false);
                  setDraft("");
                }}
              >
                取消
              </button>
              <button
                type="button"
                className="btn-primary"
                disabled={!draft.trim() || reviseMutation.isPending}
                onClick={() => reviseMutation.mutate()}
              >
                {reviseMutation.isPending ? "提交中…" : "提交修订"}
              </button>
            </div>
          </div>
        </div>
      ) : (
        <div className="mt-3 border-t border-surface-border pt-3">
          <button
            type="button"
            className="btn-secondary"
            onClick={() => {
              setDraft(plan?.content_md ?? "");
              setEditing(true);
            }}
          >
            修订计划
          </button>
        </div>
      )}
    </div>
  );
}
